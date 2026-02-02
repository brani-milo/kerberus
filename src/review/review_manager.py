"""
Review Manager for KERBERUS Tabular Review.

Manages review sessions, stores results in encrypted dossier,
and provides CRUD operations for reviews.
"""

import logging
import json
import uuid
from datetime import datetime
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field, asdict
from pathlib import Path

from .presets import get_preset, ReviewPreset
from .schema_extractor import DocumentExtraction

logger = logging.getLogger(__name__)


@dataclass
class ReviewRow:
    """Single row in a review table (one document)."""
    row_id: str
    document_id: str
    filename: str
    fields: Dict[str, Dict]  # field_name -> {value, citation}
    created_at: str
    
    def to_dict(self) -> Dict:
        return asdict(self)
    
    @classmethod
    def from_extraction(cls, extraction: DocumentExtraction) -> "ReviewRow":
        """Create ReviewRow from DocumentExtraction."""
        return cls(
            row_id=str(uuid.uuid4()),
            document_id=extraction.document_id,
            filename=extraction.filename,
            fields=extraction.get_full_dict(),
            created_at=datetime.utcnow().isoformat()
        )


@dataclass
class Review:
    """Complete review session with all documents."""
    review_id: str
    user_id: str
    name: str
    preset_id: str
    preset_name: str
    rows: List[ReviewRow]
    chat_history: List[Dict]  # [{role, content, timestamp}]
    created_at: str
    updated_at: str
    status: str  # "processing", "completed", "error"
    document_count: int
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for storage."""
        return {
            "review_id": self.review_id,
            "user_id": self.user_id,
            "name": self.name,
            "preset_id": self.preset_id,
            "preset_name": self.preset_name,
            "rows": [row.to_dict() for row in self.rows],
            "chat_history": self.chat_history,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "status": self.status,
            "document_count": self.document_count
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> "Review":
        """Create Review from dictionary."""
        rows = [
            ReviewRow(
                row_id=r["row_id"],
                document_id=r["document_id"],
                filename=r["filename"],
                fields=r["fields"],
                created_at=r["created_at"]
            )
            for r in data.get("rows", [])
        ]
        
        return cls(
            review_id=data["review_id"],
            user_id=data["user_id"],
            name=data["name"],
            preset_id=data["preset_id"],
            preset_name=data["preset_name"],
            rows=rows,
            chat_history=data.get("chat_history", []),
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            status=data["status"],
            document_count=data["document_count"]
        )
    
    def get_table_data(self) -> Dict[str, Any]:
        """Get data formatted for table display."""
        preset = get_preset(self.preset_id)
        
        # Build column headers
        columns = [{"name": f.name, "display": f.display_name} for f in preset.fields]
        
        # Build rows (values only, citations available on demand)
        table_rows = []
        for idx, row in enumerate(self.rows, start=1):
            row_data = {
                "_index": idx,
                "_row_id": row.row_id,
                "_filename": row.filename
            }
            
            for col in columns:
                field_data = row.fields.get(col["name"], {})
                row_data[col["name"]] = {
                    "value": field_data.get("value"),
                    "has_citation": field_data.get("citation") is not None
                }
            
            table_rows.append(row_data)
        
        return {
            "columns": columns,
            "rows": table_rows,
            "row_count": len(self.rows)
        }
    
    def get_citation(self, row_id: str, field_name: str) -> Optional[Dict]:
        """Get citation for a specific field in a row."""
        for row in self.rows:
            if row.row_id == row_id:
                field_data = row.fields.get(field_name, {})
                return field_data.get("citation")
        return None
    
    def get_all_values_as_list(self) -> List[Dict[str, Any]]:
        """Get all row values as a flat list (for chat context)."""
        result = []
        for idx, row in enumerate(self.rows, start=1):
            row_dict = {
                "row_number": idx,
                "filename": row.filename
            }
            for field_name, field_data in row.fields.items():
                row_dict[field_name] = field_data.get("value")
            result.append(row_dict)
        return result
    
    def add_chat_message(self, role: str, content: str):
        """Add a message to chat history."""
        self.chat_history.append({
            "role": role,
            "content": content,
            "timestamp": datetime.utcnow().isoformat()
        })
        self.updated_at = datetime.utcnow().isoformat()


class ReviewManager:
    """
    Manage review sessions with encrypted storage.
    
    Reviews are stored in the user's encrypted dossier (SQLCipher).
    """
    
    MAX_DOCUMENTS_PER_REVIEW = 30
    
    def __init__(self, dossier_db=None, storage_path: Optional[str] = None):
        """
        Initialize review manager.
        
        Args:
            dossier_db: DossierDB instance for encrypted storage
            storage_path: Alternative path for file-based storage (dev mode)
        """
        self.dossier_db = dossier_db
        self.storage_path = Path(storage_path) if storage_path else None
        
        if self.storage_path:
            self.storage_path.mkdir(parents=True, exist_ok=True)
            
        self._memory_cache: Dict[str, Review] = {}  # In-memory for dev
    
    def create_review(
        self,
        user_id: str,
        name: str,
        preset_id: str
    ) -> Review:
        """
        Create a new review session.
        
        Args:
            user_id: User ID
            name: Review name (e.g., "Q1 Contract Review")
            preset_id: Which preset to use
            
        Returns:
            New Review instance
        """
        preset = get_preset(preset_id)
        
        now = datetime.utcnow().isoformat()
        review = Review(
            review_id=str(uuid.uuid4()),
            user_id=user_id,
            name=name,
            preset_id=preset_id,
            preset_name=preset.name,
            rows=[],
            chat_history=[],
            created_at=now,
            updated_at=now,
            status="processing",
            document_count=0
        )
        
        # Save immediately
        self._save_review(review)
        
        logger.info(f"Created review {review.review_id} for user {user_id}")
        
        return review
    
    def add_extraction(
        self,
        review_id: str,
        extraction: DocumentExtraction
    ) -> ReviewRow:
        """
        Add an extracted document to a review.
        
        Args:
            review_id: Review to add to
            extraction: Extraction result
            
        Returns:
            New ReviewRow
        """
        review = self.get_review(review_id)
        
        if len(review.rows) >= self.MAX_DOCUMENTS_PER_REVIEW:
            raise ValueError(f"Review already has maximum {self.MAX_DOCUMENTS_PER_REVIEW} documents")
        
        row = ReviewRow.from_extraction(extraction)
        review.rows.append(row)
        review.document_count = len(review.rows)
        review.updated_at = datetime.utcnow().isoformat()
        
        self._save_review(review)
        
        return row
    
    def complete_review(self, review_id: str):
        """Mark a review as completed."""
        review = self.get_review(review_id)
        review.status = "completed"
        review.updated_at = datetime.utcnow().isoformat()
        self._save_review(review)
    
    def get_review(self, review_id: str) -> Review:
        """
        Get a review by ID.
        
        Args:
            review_id: Review ID
            
        Returns:
            Review instance
            
        Raises:
            ValueError: If review not found
        """
        # Check memory cache first
        if review_id in self._memory_cache:
            return self._memory_cache[review_id]
        
        # Try file storage
        if self.storage_path:
            file_path = self.storage_path / f"review_{review_id}.json"
            if file_path.exists():
                with open(file_path, 'r') as f:
                    data = json.load(f)
                review = Review.from_dict(data)
                self._memory_cache[review_id] = review
                return review
        
        # Try dossier DB
        if self.dossier_db:
            # Would use dossier_db.get_document() with review ID
            # For now, not implemented
            pass
        
        raise ValueError(f"Review not found: {review_id}")
    
    def list_reviews(self, user_id: str) -> List[Dict]:
        """
        List all reviews for a user.
        
        Args:
            user_id: User ID
            
        Returns:
            List of review summaries
        """
        reviews = []
        
        # From file storage
        if self.storage_path:
            for file_path in self.storage_path.glob("review_*.json"):
                try:
                    with open(file_path, 'r') as f:
                        data = json.load(f)
                    if data.get("user_id") == user_id:
                        reviews.append({
                            "review_id": data["review_id"],
                            "name": data["name"],
                            "preset_name": data["preset_name"],
                            "document_count": data["document_count"],
                            "status": data["status"],
                            "created_at": data["created_at"],
                            "updated_at": data["updated_at"]
                        })
                except Exception as e:
                    logger.warning(f"Error reading {file_path}: {e}")
        
        # From memory cache
        for review in self._memory_cache.values():
            if review.user_id == user_id:
                found = any(r["review_id"] == review.review_id for r in reviews)
                if not found:
                    reviews.append({
                        "review_id": review.review_id,
                        "name": review.name,
                        "preset_name": review.preset_name,
                        "document_count": review.document_count,
                        "status": review.status,
                        "created_at": review.created_at,
                        "updated_at": review.updated_at
                    })
        
        # Sort by updated_at descending
        reviews.sort(key=lambda x: x["updated_at"], reverse=True)
        
        return reviews
    
    def delete_review(self, review_id: str, user_id: str) -> bool:
        """
        Delete a review.
        
        Args:
            review_id: Review to delete
            user_id: User ID (for authorization check)
            
        Returns:
            True if deleted
        """
        try:
            review = self.get_review(review_id)
            
            if review.user_id != user_id:
                raise ValueError("Not authorized to delete this review")
            
            # Remove from memory cache
            if review_id in self._memory_cache:
                del self._memory_cache[review_id]
            
            # Remove from file storage
            if self.storage_path:
                file_path = self.storage_path / f"review_{review_id}.json"
                if file_path.exists():
                    file_path.unlink()
            
            logger.info(f"Deleted review {review_id}")
            return True
            
        except ValueError:
            return False
    
    def _save_review(self, review: Review):
        """Save review to storage."""
        # Always cache in memory
        self._memory_cache[review.review_id] = review
        
        # Save to file storage
        if self.storage_path:
            file_path = self.storage_path / f"review_{review.review_id}.json"
            with open(file_path, 'w') as f:
                json.dump(review.to_dict(), f, indent=2)
        
        # Save to dossier DB
        if self.dossier_db:
            # Would use dossier_db.store_document()
            # For now, not implemented
            pass
    
    def update_chat(self, review_id: str, role: str, content: str):
        """Add a chat message to review history."""
        review = self.get_review(review_id)
        review.add_chat_message(role, content)
        self._save_review(review)
