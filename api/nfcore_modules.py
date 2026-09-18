"""
nf-core module name normalization.

Uses nf-core.cache file for official module names.
"""

import os
from typing import Optional, Set

# Path to nf-core cache file
CACHE_FILE = os.path.join(os.path.dirname(__file__), 'nf-core.cache')

# Load nf-core modules from cache
NFCORE_MODULES: Set[str] = set()

def _load_cache():
    """Load nf-core modules from cache file."""
    global NFCORE_MODULES
    if NFCORE_MODULES:
        return
    
    cache_path = os.path.join(os.path.dirname(__file__), 'nf-core.cache')
    if not os.path.exists(cache_path):
        cache_path = '/code/nf-core.cache'
    
    if not os.path.exists(cache_path):
        cache_path = os.path.join(os.path.dirname(__file__), 'nf-core.cache')
    
    try:
        with open(cache_path, 'r') as f:
            for line in f:
                if '│' in line and '/' in line:
                    parts = line.split('│')
                    if len(parts) >= 2:
                        module = parts[1].strip()
                        if module and '/' in module:
                            tool, subtool = module.split('/', 1)
                            module_name = f"{tool.upper()}_{subtool.upper().replace('-', '_')}"
                            NFCORE_MODULES.add(module_name)
    except Exception as e:
        pass


def normalize_module_name(process_name: str, nfcore_cache=None) -> str:
    """
    Normalize process name to nf-core TOOL_SUBTOOL format.
    
    If the tool is in nf-core cache, keep TOOL_SUBTOOL.
    Otherwise return as-is.
    """
    _load_cache()
    
    # Step 1: Remove instance suffixes like " (test1)"
    base = process_name.split(' (')[0] if ' (' in process_name else process_name
    
    # Step 2: Get module name (last part after colon)
    if ':' in base:
        module = base.split(':')[-1]
    else:
        module = base
    
    # Step 3: Convert to uppercase
    module = module.upper()
    
    # Step 4: Split parts
    parts = module.split('_')
    
    if len(parts) >= 2:
        tool = parts[0]
        subtool = parts[1]
        
        # Remove numeric suffix from subtool
        subtool_clean = subtool.rstrip('0123456789')
        
        candidate = f'{tool}_{subtool_clean}'
        
        # Check if exact match in nf-core
        if candidate in NFCORE_MODULES:
            return candidate
        
        # In this case, try to find the correct subtool
        if tool in subtool_clean and len(subtool_clean) > len(tool):
            # Find matching nf-core module for this tool
            for nf_module in NFCORE_MODULES:
                if nf_module.startswith(f'{tool}_'):
                    nf_subtool = nf_module.split('_', 1)[1]
                    # Check if the garbage subtool contains this subtool
                    if nf_subtool in subtool_clean:
                        return f'{tool}_{nf_subtool}'
            
            # Just return first valid subtool for this tool
            for nf_module in sorted(NFCORE_MODULES):
                if nf_module.startswith(f'{tool}_'):
                    return nf_module
        
        # Check if tool exists in nf-core
        tool_modules = [m for m in NFCORE_MODULES if m.startswith(f'{tool}_')]
        if tool_modules:
            return f'{tool}_{subtool_clean}'
    
    # Not an nf-core tool, return as-is
    return module


def get_module_display_name(module_name: str) -> str:
    """Get human-readable display name."""
    return module_name.replace('_', ' ')

def get_nfcore_modules():
    """Return the loaded nf-core modules set."""
    _load_cache()
    return list(NFCORE_MODULES)
