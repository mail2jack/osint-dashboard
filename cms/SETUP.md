"""
CMS Setup Guide
================
Instructions for integrating the Case Management System with existing Flask app.

================================================================================
STEP 1: Database Setup (PostgreSQL)
================================================================================

1. Install PostgreSQL if not already installed
2. Create a database:
   ```sql
   CREATE DATABASE cms_db;
   CREATE USER cms_user WITH PASSWORD 'your_secure_password';
   GRANT ALL PRIVILEGES ON DATABASE cms_db TO cms_user;
   ```

3. Set environment variables:
   ```bash
   export DATABASE_URL="postgresql://cms_user:your_password@localhost:5432/cms_db"
   ```

================================================================================
STEP 2: Generate Encryption Key
================================================================================

Generate a secure encryption key for field-level encryption:
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Add to your environment:
```bash
export CMS_ENCRYPTION_KEY="your-generated-key-here"
```

================================================================================
STEP 3: Update app.py
================================================================================

Add to your existing app.py:

```python
from cms import create_cms_module, init_db
from cms.config import get_config

# Load environment
from dotenv import load_dotenv
load_dotenv()

# Initialize database
init_db(app)

# Initialize CMS module
create_cms_module(app)
```

================================================================================
STEP 4: Run Migrations
================================================================================

```bash
export FLASK_APP=app.py
flask db init
flask db migrate -m "Initial CMS migration"
flask db upgrade
```

================================================================================
STEP 5: Create Templates
================================================================================

Create the following templates in templates/cms/:
- login.html
- dashboard.html
- clients/list.html, view.html, create.html, edit.html
- cases/list.html, view.html, create.html, edit.html
- subjects/list.html, view.html, create.html, edit.html
- audit/log.html
- users/list.html, view.html, create.html, edit.html

================================================================================
STEP 6: Update .env
================================================================================

Add to your .env file:
```bash
# Database
DATABASE_URL=postgresql://cms_user:password@localhost:5432/cms_db

# CMS Security
CMS_ENCRYPTION_KEY=your-32-byte-url-safe-base64-encoded-key
CMS_API_KEY=your-api-key-for-programmatic-access

# Session
SECRET_KEY=your-secret-key-for-sessions
```

================================================================================
SECURITY CHECKLIST
================================================================================

[ ] PostgreSQL configured with proper authentication
[ ] Encryption key set in production environment
[ ] HTTPS enforced in production (SESSION_COOKIE_SECURE=True)
[ ] Default admin password changed
[ ] Regular database backups scheduled
[ ] Audit logs reviewed periodically
[ ] User roles properly assigned

================================================================================
ENVIRONMENT VARIABLES
================================================================================

Required:
- CMS_ENCRYPTION_KEY: Fernet encryption key for sensitive fields
- DATABASE_URL: PostgreSQL connection string

Optional:
- CMS_API_KEY: For API programmatic access
- SECRET_KEY: Flask session secret (auto-generated if not set)
- DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD: Alternative to DATABASE_URL

================================================================================
"""
