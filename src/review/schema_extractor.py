"""
Schema Extractor for KERBERUS Tabular Review.

Uses Qwen (without websearch) to extract structured data from documents
with citations for each field.
"""

import logging
import json
import os
import re
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field
import httpx

from .presets import ReviewPreset, get_preset
from .document_processor import ParsedDocument

logger = logging.getLogger(__name__)


@dataclass
class FieldExtraction:
    """Result of extracting a single field."""
    field_name: str
    value: Any
    citation: Optional[Dict[str, Any]] = None  # {page, section, quote}
    confidence: float = 1.0
    
    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "value": self.value,
            "citation": self.citation,
            "confidence": self.confidence
        }


@dataclass
class DocumentExtraction:
    """Complete extraction results for a document."""
    document_id: str
    filename: str
    preset_id: str
    fields: Dict[str, FieldExtraction]
    extraction_errors: List[str] = field(default_factory=list)
    
    def get_values_dict(self) -> Dict[str, Any]:
        """Get just the values without citations."""
        return {k: v.value for k, v in self.fields.items()}
    
    def get_full_dict(self) -> Dict[str, Dict]:
        """Get values with citations."""
        return {k: v.to_dict() for k, v in self.fields.items()}


class SchemaExtractor:
    """
    Extract structured data from documents using Qwen LLM.
    
    Uses the Qwen model WITHOUT websearch for document extraction.
    Each field extraction includes a citation to the source text.
    """
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "qwen-plus",
        base_url: str = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        timeout: int = 120
    ):
        """
        Initialize extractor.
        
        Args:
            api_key: Qwen API key (falls back to QWEN_API_KEY env var)
            model: Model to use (qwen-plus, qwen-turbo, etc.)
            base_url: API base URL
            timeout: Request timeout in seconds
        """
        self.api_key = api_key or os.environ.get("QWEN_API_KEY")
        if not self.api_key:
            raise ValueError("QWEN_API_KEY not set. Provide api_key or set environment variable.")
            
        self.model = model
        self.base_url = base_url
        self.timeout = timeout
        
        logger.info(f"SchemaExtractor initialized with model: {model}")
    
    async def extract_document(
        self,
        document: ParsedDocument,
        preset_id: str
    ) -> DocumentExtraction:
        """
        Extract structured data from a document using the specified preset.
        
        Args:
            document: Parsed document with full text
            preset_id: ID of the preset schema to use
            
        Returns:
            DocumentExtraction with all fields and citations
        """
        preset = get_preset(preset_id)
        
        # Build extraction prompt
        prompt = self._build_extraction_prompt(document, preset)
        
        # Call LLM
        try:
            response = await self._call_qwen(prompt)
            
            # Parse response
            extraction = self._parse_extraction_response(
                response, document, preset
            )
            
            return extraction
            
        except Exception as e:
            logger.error(f"Extraction failed for {document.filename}: {e}")
            # Return empty extraction with error
            return DocumentExtraction(
                document_id=document.document_id,
                filename=document.filename,
                preset_id=preset_id,
                fields={},
                extraction_errors=[str(e)]
            )
    
    def extract_document_sync(
        self,
        document: ParsedDocument,
        preset_id: str
    ) -> DocumentExtraction:
        """Synchronous version of extract_document."""
        import asyncio
        return asyncio.run(self.extract_document(document, preset_id))
    
    def _build_extraction_prompt(
        self,
        document: ParsedDocument,
        preset: ReviewPreset
    ) -> str:
        """Build the extraction prompt for Qwen."""
        
        schema_description = preset.to_prompt_schema()
        
        # Truncate document if too long
        max_doc_length = 30000  # Leave room for prompt and response
        doc_text = document.full_text
        if len(doc_text) > max_doc_length:
            doc_text = doc_text[:max_doc_length] + "\n\n[Document truncated due to length...]"
        
        prompt = f"""You are a legal document analyst. Extract structured information from the following document.

## TASK
Analyze the document and extract the following fields. For EACH field, provide:
1. The extracted value
2. A citation with the exact quote and approximate page/section reference

## SCHEMA TO EXTRACT
{schema_description}

## OUTPUT FORMAT
Return a JSON object where each key is a field name, and the value is an object with:
- "value": the extracted value (use null if not found)
- "page": page number or section where found (use 1 if unknown)
- "section": section reference if identifiable (e.g., "§4.1", "Article 3", or null)
- "quote": the exact text (max 150 chars) that supports this value

Example:
```json
{{
  "party_a_name": {{
    "value": "Acme Corporation",
    "page": 1,
    "section": "Preamble",
    "quote": "This Agreement is entered into by and between Acme Corporation, a Delaware corporation..."
  }},
  "contract_value": {{
    "value": "CHF 500,000",
    "page": 3,
    "section": "§4.1",
    "quote": "The total consideration shall not exceed five hundred thousand Swiss Francs (CHF 500,000.00)"
  }}
}}
```

## DOCUMENT TO ANALYZE
Filename: {document.filename}
Total Pages: {document.total_pages}
---
{doc_text}
---

## IMPORTANT INSTRUCTIONS
1. Extract ALL fields from the schema, even if value is null
2. Be precise with values - use exact amounts, dates, names as written
3. For boolean fields, use true/false
4. For enum fields, use one of the allowed values exactly
5. Always include a citation quote for non-null values
6. If information is not in the document, set value to null with no quote
7. Return ONLY valid JSON, no additional text

Now extract all fields from the document:"""

        return prompt
    
    async def _call_qwen(self, prompt: str) -> str:
        """Call Qwen API and get response."""
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a precise legal document analyst. You extract structured information with exact citations. Always respond with valid JSON only."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "temperature": 0.1,  # Low temperature for precise extraction
            "max_tokens": 8000,
            "response_format": {"type": "json_object"}
        }
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload
            )
            
            if response.status_code != 200:
                error_text = response.text
                logger.error(f"Qwen API error: {response.status_code} - {error_text}")
                raise Exception(f"Qwen API error: {response.status_code}")
                
            result = response.json()
            content = result["choices"][0]["message"]["content"]
            
            return content
    
    def _parse_extraction_response(
        self,
        response: str,
        document: ParsedDocument,
        preset: ReviewPreset
    ) -> DocumentExtraction:
        """Parse LLM response into structured extraction."""
        
        # Clean response (remove markdown code blocks if present)
        response = response.strip()
        if response.startswith("```json"):
            response = response[7:]
        if response.startswith("```"):
            response = response[3:]
        if response.endswith("```"):
            response = response[:-3]
        response = response.strip()
        
        try:
            data = json.loads(response)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON response: {e}")
            logger.debug(f"Response was: {response[:500]}...")
            return DocumentExtraction(
                document_id=document.document_id,
                filename=document.filename,
                preset_id=preset.id,
                fields={},
                extraction_errors=[f"JSON parse error: {e}"]
            )
        
        # Build field extractions
        fields = {}
        errors = []
        
        for field_def in preset.fields:
            field_name = field_def.name
            
            if field_name in data:
                field_data = data[field_name]
                
                if isinstance(field_data, dict):
                    # Structured response with citation
                    value = field_data.get("value")
                    citation = None
                    
                    if field_data.get("quote"):
                        citation = {
                            "page": field_data.get("page", 1),
                            "section": field_data.get("section"),
                            "quote": field_data.get("quote", "")[:200]
                        }
                        
                        # Try to find and verify citation in document
                        if citation["quote"]:
                            found = document.get_context_around(citation["quote"][:50])
                            if found:
                                citation["page"] = found["page"]
                    
                    fields[field_name] = FieldExtraction(
                        field_name=field_name,
                        value=value,
                        citation=citation
                    )
                else:
                    # Simple value without citation
                    fields[field_name] = FieldExtraction(
                        field_name=field_name,
                        value=field_data,
                        citation=None
                    )
            else:
                # Field not in response
                fields[field_name] = FieldExtraction(
                    field_name=field_name,
                    value=None,
                    citation=None
                )
        
        return DocumentExtraction(
            document_id=document.document_id,
            filename=document.filename,
            preset_id=preset.id,
            fields=fields,
            extraction_errors=errors
        )
    
    async def extract_batch(
        self,
        documents: List[ParsedDocument],
        preset_id: str,
        max_concurrent: int = 3,
        progress_callback: Optional[callable] = None
    ) -> List[DocumentExtraction]:
        """
        Extract from multiple documents with concurrency control.
        
        Args:
            documents: List of parsed documents
            preset_id: Preset to use for all documents
            max_concurrent: Maximum concurrent extractions
            progress_callback: Called with (completed, total) after each document
            
        Returns:
            List of extractions in same order as documents
        """
        import asyncio
        
        results = []
        semaphore = asyncio.Semaphore(max_concurrent)
        
        async def extract_with_semaphore(doc: ParsedDocument, index: int):
            async with semaphore:
                result = await self.extract_document(doc, preset_id)
                if progress_callback:
                    progress_callback(index + 1, len(documents))
                return result
        
        tasks = [
            extract_with_semaphore(doc, i)
            for i, doc in enumerate(documents)
        ]
        
        results = await asyncio.gather(*tasks)
        
        return list(results)
