"""
Core logic module for the automated task.
"""

import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def perform_task(input_data: str) -> str:
    """
    Performs the core task logic.
    """
    if not input_data:
        raise ValueError("Input data cannot be empty.")
    
    result = f"Processed: {input_data.upper()}"
    logger.info(f"Task completed: {result}")
    return result

if __name__ == "__main__":
    import sys
    data = sys.argv[1] if len(sys.argv) > 1 else "default"
    print(perform_task(data))
