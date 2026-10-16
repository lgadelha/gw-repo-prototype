#!/usr/bin/env python3
"""
Setup script for GW-Repo.
Generates .env file with API key and starts the services.
"""

import os
import secrets
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
ENV_FILE = PROJECT_ROOT / ".env"

def generate_api_key():
    """Generate a secure random API key."""
    return secrets.token_urlsafe(32)

def create_env_file():
    """Create .env file with auto-generated API key."""
    api_key = generate_api_key()
    
    env_content = f"""# ===========================================
# GW-Repo Configuration
# ===========================================
# Auto-generated on first run.
# Regenerate by deleting this file and running: ./setup.sh
# ===========================================

# API Authentication
API_KEY={api_key}

# Database Configuration
DATABASE_URL=postgresql://postgres:local_dev_pass_123@db/gw_repo
POSTGRES_PASSWORD=local_dev_pass_123
POSTGRES_DB=gw_repo

# Client Configuration
API_BASE_URL=http://localhost:80
"""
    
    with open(ENV_FILE, "w") as f:
        f.write(env_content)
    
    print("=" * 60)
    print("GW-Repo Setup Complete!")
    print("=" * 60)
    print()
    print(f"✓ Configuration file created: {ENV_FILE}")
    print(f"✓ API Key generated: {api_key}")
    print()
    print("To start the services, run:")
    print("  docker compose up -d")
    print()
    print("Your API key has been saved to .env")
    print("You can also export it for CLI usage:")
    print(f"  export API_KEY={api_key}")
    print()

if __name__ == "__main__":
    if ENV_FILE.exists():
        print(f"Configuration file already exists: {ENV_FILE}")
        print("Delete it and run this script again to regenerate.")
    else:
        create_env_file()
