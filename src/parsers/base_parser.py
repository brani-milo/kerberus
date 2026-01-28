
import json
import logging
from abc import ABC, abstractmethod
from typing import Dict, Any, Union
from pathlib import Path

logger = logging.getLogger(__name__)

class BaseParser(ABC):
    """
    Abstract Base Class for all parsers.
    Defines the standard output schema and common methods.
    """

    def save_json(self, data: Dict[str, Any], output_path: Union[str, Path]) -> None:
        """
        Saves the parsed data to a JSON file.
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            logger.info(f"Successfully saved parsed data to {output_path}")
        except Exception as e:
            logger.error(f"Failed to save JSON to {output_path}: {e}")
            raise

    @abstractmethod
    def parse(self, file_path: Union[str, Path]) -> Dict[str, Any]:
        """
        Parses the input file and returns a dictionary matching the schema.
        Must be implemented by subclasses.
        """
        pass

    def _get_empty_schema(self) -> Dict[str, Any]:
        """
        Returns the standard empty schema structure.
        """
        return {
            "id": None,
            "file_name": None,
            "date": None,
            "year": None,
            "language": None,
            "court": None,
            "outcome": None,
            "metadata": {
                "judges": [],
                "citations": { "cases": [], "laws": [] },
                "lower_court": None
            },
            "content": {
                "regeste": None,
                "facts": None,
                "reasoning": None,
                "decision": None
            }
        }
