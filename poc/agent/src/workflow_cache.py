import hashlib
import json
import re
import sys
from pathlib import Path


def get_workflows_dir():
    """Get the workflows cache directory."""
    workflows_dir = Path.home() / ".openship" / "workflows"
    workflows_dir.mkdir(parents=True, exist_ok=True)
    return workflows_dir


def compute_checksum(content: bytes) -> str:
    """Compute SHA-256 checksum of bytes."""
    return hashlib.sha256(content).hexdigest()


def compute_file_checksum(file_path: str) -> str:
    """Compute SHA-256 checksum of a file."""
    with open(file_path, 'rb') as f:
        content = f.read()
    return compute_checksum(content)


def compute_signature(input_path: str, json_content: str) -> str:
    """
    Compute signature binding input file and JSON cache content.
    
    Signature = SHA-256(input_file_content + json_cache_content)
    
    This ensures that if either the input file or the cached JSON changes,
    the signature will no longer match.
    """
    with open(input_path, 'rb') as f:
        input_content = f.read()
    
    signature_input = input_content + json_content.encode()
    return compute_checksum(signature_input)


def save_cached_workflow(input_path: str, plan_json: dict) -> str:
    """
    Save compiled workflow to cache with integrity signature.
    
    Args:
        input_path: Path to the input workflow file
        plan_json: The compiled plan JSON to cache
    
    Returns:
        The cache file path
    """
    # Compute input file hash (for lookup)
    input_hash = compute_file_checksum(input_path)
    
    # Compute signature binding input and JSON content
    json_content = json.dumps(plan_json)
    signature = compute_signature(input_path, json_content)
    
    # Build cache filename: <input_hash>.<signature>.json
    workflows_dir = get_workflows_dir()
    cache_file = workflows_dir / f"{input_hash}.{signature}.json"
    
    # Save cached workflow
    cache_file.write_text(json_content)
    print(f"Workflow cached to: {cache_file}")
    return str(cache_file)


def load_cached_workflow(input_path: str):
    """
    Load cached workflow by input file hash.
    
    Uses glob search to find the cache file, then verifies the signature.
    
    Args:
        input_path: Path to the input workflow file
    
    Returns:
        Tuple of (cache_file, plan_json) if found and valid, None otherwise
    """
    # Compute input file hash
    input_hash = compute_file_checksum(input_path)
    
    # Glob search for cache file with matching input hash
    workflows_dir = get_workflows_dir()
    pattern = f"{input_hash}.*.json"
    matches = list(workflows_dir.glob(pattern))
    
    if not matches:
        return None
    
    # Check each match for valid signature
    for cache_file in matches:
        # Parse signature from filename
        filename_parts = cache_file.name.split(".")
        # Filename format: <input_hash>.<signature>.json
        if len(filename_parts) >= 3 and filename_parts[0] == input_hash:
            signature = filename_parts[1]
            
            # Read JSON content
            json_content = cache_file.read_text()
            
            # Verify signature
            computed_signature = compute_signature(input_path, json_content)
            if computed_signature == signature:
                plan_json = json.loads(json_content)
                return (str(cache_file), plan_json)
    
    return None