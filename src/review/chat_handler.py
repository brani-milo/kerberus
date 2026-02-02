"""
Chat Handler for KERBERUS Tabular Review.

Enables conversational queries on review data using Qwen.
Users can ask analytical questions about the extracted table.
"""

import logging
import json
import os
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
import httpx

from .review_manager import Review
from .presets import get_preset

logger = logging.getLogger(__name__)


@dataclass
class ChatResponse:
    """Response from a chat query."""
    answer: str
    citations: List[Dict]  # [{row_id, field_name, value, document}]
    suggested_followups: List[str]


class ReviewChatHandler:
    """
    Handle conversational queries on review data.
    
    Uses Qwen to answer questions based on the extracted table data.
    Provides citations linking back to specific documents and fields.
    """
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "qwen-plus",
        base_url: str = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        timeout: int = 60
    ):
        """
        Initialize chat handler.
        
        Args:
            api_key: Qwen API key (falls back to QWEN_API_KEY env var)
            model: Model to use
            base_url: API base URL
            timeout: Request timeout
        """
        self.api_key = api_key or os.environ.get("QWEN_API_KEY")
        if not self.api_key:
            raise ValueError("QWEN_API_KEY not set")
            
        self.model = model
        self.base_url = base_url
        self.timeout = timeout
    
    async def ask(
        self,
        review: Review,
        question: str,
        include_history: bool = True
    ) -> ChatResponse:
        """
        Ask a question about the review data.
        
        Args:
            review: Review with extracted data
            question: User's question
            include_history: Include previous chat messages for context
            
        Returns:
            ChatResponse with answer and citations
        """
        # Build context from review data
        context = self._build_context(review)
        
        # Build messages
        messages = self._build_messages(review, question, context, include_history)
        
        # Call LLM
        response = await self._call_qwen(messages)
        
        # Parse response
        chat_response = self._parse_response(response, review)
        
        # Save to chat history
        review.add_chat_message("user", question)
        review.add_chat_message("assistant", chat_response.answer)
        
        return chat_response
    
    def ask_sync(
        self,
        review: Review,
        question: str,
        include_history: bool = True
    ) -> ChatResponse:
        """Synchronous version of ask."""
        import asyncio
        return asyncio.run(self.ask(review, question, include_history))
    
    def _build_context(self, review: Review) -> str:
        """Build context string from review data."""
        preset = get_preset(review.preset_id)
        
        # Get column names
        columns = [f.display_name for f in preset.fields]
        
        # Build table representation
        lines = [
            f"## Review: {review.name}",
            f"Preset: {review.preset_name}",
            f"Documents: {review.document_count}",
            "",
            "## Data Table",
            ""
        ]
        
        # Add as structured data for LLM
        for idx, row in enumerate(review.rows, start=1):
            lines.append(f"### Document {idx}: {row.filename}")
            
            for field_def in preset.fields:
                field_data = row.fields.get(field_def.name, {})
                value = field_data.get("value")
                
                if value is not None:
                    # Format value for display
                    if isinstance(value, bool):
                        display = "Yes" if value else "No"
                    elif isinstance(value, list):
                        display = ", ".join(str(v) for v in value)
                    else:
                        display = str(value)
                    
                    lines.append(f"- **{field_def.display_name}**: {display}")
            
            lines.append("")
        
        return "\n".join(lines)
    
    def _build_messages(
        self,
        review: Review,
        question: str,
        context: str,
        include_history: bool
    ) -> List[Dict]:
        """Build message list for LLM."""
        
        messages = [
            {
                "role": "system",
                "content": """You are a legal analyst assistant helping review extracted document data.

You have access to a table of extracted information from multiple documents.
Answer the user's questions based on this data.

IMPORTANT RULES:
1. Base your answers ONLY on the provided data
2. When referencing specific documents, cite them by number and filename
3. For numerical analysis, show your calculations
4. If the data doesn't contain enough information, say so
5. Suggest follow-up questions when relevant
6. Format your response in clear markdown

OUTPUT FORMAT:
Start with your answer, then if you referenced specific documents, add:

**Sources:**
- Document X: [filename] - [relevant field/value]
"""
            }
        ]
        
        # Add context as first user message
        messages.append({
            "role": "user",
            "content": f"Here is the review data to analyze:\n\n{context}"
        })
        
        messages.append({
            "role": "assistant",
            "content": "I've reviewed the data. I can help you analyze these documents. What would you like to know?"
        })
        
        # Add chat history if requested
        if include_history:
            for msg in review.chat_history[-10:]:  # Last 10 messages
                messages.append({
                    "role": msg["role"],
                    "content": msg["content"]
                })
        
        # Add current question
        messages.append({
            "role": "user",
            "content": question
        })
        
        return messages
    
    async def _call_qwen(self, messages: List[Dict]) -> str:
        """Call Qwen API."""
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.3,  # Balanced for analysis
            "max_tokens": 2000
        }
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload
            )
            
            if response.status_code != 200:
                logger.error(f"Qwen API error: {response.status_code}")
                raise Exception(f"Qwen API error: {response.status_code}")
                
            result = response.json()
            return result["choices"][0]["message"]["content"]
    
    def _parse_response(self, response: str, review: Review) -> ChatResponse:
        """Parse LLM response and extract citations."""
        
        citations = []
        
        # Try to extract document references from response
        # Look for patterns like "Document 1", "Document #1", etc.
        import re
        
        doc_refs = re.findall(r'Document\s*#?\s*(\d+)', response, re.IGNORECASE)
        
        for ref in set(doc_refs):
            try:
                idx = int(ref) - 1
                if 0 <= idx < len(review.rows):
                    row = review.rows[idx]
                    citations.append({
                        "row_id": row.row_id,
                        "document_number": int(ref),
                        "filename": row.filename
                    })
            except (ValueError, IndexError):
                pass
        
        # Generate suggested follow-ups based on question type
        suggested_followups = self._generate_followups(response, review)
        
        return ChatResponse(
            answer=response,
            citations=citations,
            suggested_followups=suggested_followups
        )
    
    def _generate_followups(self, response: str, review: Review) -> List[str]:
        """Generate suggested follow-up questions."""
        
        preset = get_preset(review.preset_id)
        
        # Base suggestions on preset type
        suggestions = []
        
        if preset.id == "contract_review":
            suggestions = [
                "Which contracts have the highest risk level?",
                "What is the total value of all contracts?",
                "Which contracts expire in the next 6 months?",
                "Do any contracts have change of control clauses?",
            ]
        elif preset.id == "due_diligence":
            suggestions = [
                "What are the main red flags identified?",
                "Are there any deal breakers?",
                "Which entities have pending litigation?",
                "What is the overall recommendation?",
            ]
        elif preset.id == "employment_contracts":
            suggestions = [
                "Which employees have non-compete clauses?",
                "What is the average notice period?",
                "Which contracts have equity grants?",
                "Who has the highest salary?",
            ]
        elif preset.id == "nda_review":
            suggestions = [
                "Which NDAs are mutual vs one-way?",
                "What is the longest confidentiality period?",
                "Which NDAs have residuals clauses?",
                "Are there any concerning provisions?",
            ]
        elif preset.id == "court_case_summary":
            suggestions = [
                "Which cases have landmark precedent value?",
                "What are the common legal issues?",
                "Which court issued the most decisions?",
                "What were the most common outcomes?",
            ]
        elif preset.id == "document_discovery":
            suggestions = [
                "How many documents are privileged?",
                "Which documents are marked as 'Hot'?",
                "What is the breakdown by document type?",
                "Which custodians have the most documents?",
            ]
        
        # Return first 3 that aren't similar to current response
        return suggestions[:3]


class StreamingChatHandler(ReviewChatHandler):
    """
    Streaming version of chat handler for real-time responses.
    """
    
    async def ask_stream(
        self,
        review: Review,
        question: str,
        include_history: bool = True
    ):
        """
        Stream a response token by token.
        
        Yields:
            Chunks of the response as they arrive
        """
        context = self._build_context(review)
        messages = self._build_messages(review, question, context, include_history)
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": 2000,
            "stream": True
        }
        
        full_response = ""
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload
            ) as response:
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data = line[6:]
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                            delta = chunk["choices"][0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                full_response += content
                                yield content
                        except json.JSONDecodeError:
                            continue
        
        # Save to history after streaming complete
        review.add_chat_message("user", question)
        review.add_chat_message("assistant", full_response)
