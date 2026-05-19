"""
Case Management System (CMS) Blueprint
=======================================
Professional investigation management for legal and forensic applications.

This module integrates with the existing Flask OSINT application,
providing:
- Role-Based Access Control (RBAC)
- Field-Level Encryption for GDPR compliance
- Comprehensive Audit Logging
- Case and Client Management
- Subject and Financial Tracking
"""

from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_migrate import Migrate

from .models import db, User
from .auth import login_manager


migrate = Migrate()


def create_cms_module(app: Flask):
    """
    Initialize CMS module with Flask application.
    
    Args:
        app: Flask application instance
    """
    # Initialize extensions
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    
    # Configure login
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Please log in to access the Case Management System.'
    login_manager.login_message_category = 'info'
    
    # Register blueprints
    from .routes import cms_bp
    from .auth import auth_bp, users_bp
    
    app.register_blueprint(cms_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(users_bp)
    
    # Inject theme_style into all templates
    @app.context_processor
    def inject_theme():
        from .models import Setting
        style = Setting.get('theme_style', 'classic')
        return {'theme_style': style}
    
    # Create tables if they don't exist
    with app.app_context():
        try:
            db.create_all()
        except Exception as e:
            if 'already exists' not in str(e):
                raise
            app.logger.warning(f"Table creation race (harmless): {e}")
        
        # Migration: add address_number column to clients if missing
        try:
            from sqlalchemy import inspect, text
            inspector = inspect(db.engine)
            columns = [c['name'] for c in inspector.get_columns('clients')]
            if 'address_number' not in columns:
                db.session.execute(text('ALTER TABLE clients ADD COLUMN address_number VARCHAR(20)'))
                db.session.commit()
                app.logger.info("Migration: added address_number column to clients table")
            if 'date_of_birth' not in columns:
                db.session.execute(text('ALTER TABLE clients ADD COLUMN date_of_birth VARCHAR(500)'))
                app.logger.info("Migration: added date_of_birth column to clients table")
            if 'place_of_birth' not in columns:
                db.session.execute(text('ALTER TABLE clients ADD COLUMN place_of_birth VARCHAR(500)'))
                app.logger.info("Migration: added place_of_birth column to clients table")
            db.session.commit()
        except Exception as e:
            app.logger.warning(f"Migration note: {e}")
            db.session.rollback()
        
        # Migration: add client_id column to addresses table if missing
        try:
            from sqlalchemy import inspect, text
            inspector = inspect(db.engine)
            columns = [c['name'] for c in inspector.get_columns('addresses')]
            if 'client_id' not in columns:
                db.session.execute(text('ALTER TABLE addresses ADD COLUMN client_id VARCHAR(36)'))
                db.session.execute(text('CREATE INDEX IF NOT EXISTS ix_addresses_client_id ON addresses(client_id)'))
                db.session.commit()
                app.logger.info("Migration: added client_id column to addresses table")
        except Exception as e:
            app.logger.warning(f"Migration note (addresses.client_id): {e}")
            db.session.rollback()
        
        # Migration: allow subject_id to be nullable in addresses
        try:
            if db.engine.name == 'postgresql':
                from sqlalchemy import inspect, text
                inspector = inspect(db.engine)
                col_info = {c['name']: c for c in inspector.get_columns('addresses')}
                if col_info.get('subject_id', {}).get('nullable') == False:
                    db.session.execute(text('ALTER TABLE addresses ALTER COLUMN subject_id DROP NOT NULL'))
                db.session.commit()
                app.logger.info("Migration: made subject_id nullable in addresses table")
        except Exception as e:
            app.logger.warning(f"Migration note (subject_id nullable): {e}")
            db.session.rollback()
        
        # Migration: resize audit_logs.entity_id for two-UUID entity IDs
        try:
            if db.engine.name == 'postgresql':
                from sqlalchemy import inspect, text
                inspector = inspect(db.engine)
                audit_cols = {c['name']: c for c in inspector.get_columns('audit_logs')}
                if 'entity_id' in audit_cols:
                    col_type = str(audit_cols['entity_id']['type'])
                    if col_type.startswith('VARCHAR') and col_type not in ('VARCHAR(128)', 'VARCHAR(500)'):
                        db.session.execute(
                            text('ALTER TABLE audit_logs ALTER COLUMN entity_id TYPE VARCHAR(128)')
                        )
                        app.logger.info("Migration: resized audit_logs.entity_id to VARCHAR(128)")
                db.session.commit()
        except Exception as e:
            app.logger.warning(f"audit_logs.entity_id resize note: {e}")
            db.session.rollback()
        
        # Migration: add 2FA columns to users table if missing
        try:
            from sqlalchemy import inspect, text
            inspector = inspect(db.engine)
            columns = [c['name'] for c in inspector.get_columns('users')]
            if 'totp_secret' not in columns:
                db.session.execute(text('ALTER TABLE users ADD COLUMN totp_secret VARCHAR(64)'))
                app.logger.info("Migration: added totp_secret column to users table")
            if 'totp_enabled' not in columns:
                db.session.execute(text('ALTER TABLE users ADD COLUMN totp_enabled BOOLEAN DEFAULT 0'))
                app.logger.info("Migration: added totp_enabled column to users table")
            if 'backup_codes' not in columns:
                db.session.execute(text('ALTER TABLE users ADD COLUMN backup_codes TEXT'))
                app.logger.info("Migration: added backup_codes column to users table")
            db.session.commit()
        except Exception as e:
            app.logger.warning(f"2FA migration note: {e}")
            db.session.rollback()

        # Migration: add missing subjects columns
        try:
            from sqlalchemy import inspect, text
            inspector = inspect(db.engine)
            columns = [c['name'] for c in inspector.get_columns('subjects')]
            if 'social_media_ids' not in columns:
                db.session.execute(text('ALTER TABLE subjects ADD COLUMN social_media_ids TEXT'))
                app.logger.info("Migration: added social_media_ids column to subjects table")
            if 'rdw_data' not in columns:
                db.session.execute(text('ALTER TABLE subjects ADD COLUMN rdw_data TEXT'))
                app.logger.info("Migration: added rdw_data column to subjects table")
            if 'face_encoding' not in columns:
                db.session.execute(text('ALTER TABLE subjects ADD COLUMN face_encoding TEXT'))
                app.logger.info("Migration: added face_encoding column to subjects table")
            if 'imo_number' not in columns:
                db.session.execute(text('ALTER TABLE subjects ADD COLUMN imo_number VARCHAR(500)'))
                app.logger.info("Migration: added imo_number column to subjects table")
            if 'mmsi' not in columns:
                db.session.execute(text('ALTER TABLE subjects ADD COLUMN mmsi VARCHAR(500)'))
                app.logger.info("Migration: added mmsi column to subjects table")
            if 'eni_number' not in columns:
                db.session.execute(text('ALTER TABLE subjects ADD COLUMN eni_number VARCHAR(500)'))
                app.logger.info("Migration: added eni_number column to subjects table")
            if 'vessel_nationality' not in columns:
                db.session.execute(text('ALTER TABLE subjects ADD COLUMN vessel_nationality VARCHAR(500)'))
                app.logger.info("Migration: added vessel_nationality column to subjects table")
            if 'vessel_data' not in columns:
                db.session.execute(text('ALTER TABLE subjects ADD COLUMN vessel_data TEXT'))
                app.logger.info("Migration: added vessel_data column to subjects table")
            db.session.commit()
        except Exception as e:
            app.logger.warning(f"subjects migration note: {e}")
            db.session.rollback()

        # Migration: create social_accounts table for existing DBs
        try:
            from sqlalchemy import inspect, text
            inspector = inspect(db.engine)
            tables = inspector.get_table_names()
            if 'social_accounts' not in tables:
                db.session.execute(text('''
                    CREATE TABLE social_accounts (
                        id VARCHAR(36) PRIMARY KEY,
                        subject_id VARCHAR(36) REFERENCES subjects(id),
                        platform VARCHAR(50) NOT NULL,
                        username VARCHAR(200) NOT NULL,
                        url VARCHAR(500),
                        account_id VARCHAR(200),
                        created_at TIMESTAMP,
                        updated_at TIMESTAMP
                    )
                '''))
                db.session.execute(text('CREATE INDEX IF NOT EXISTS ix_social_accounts_subject_id ON social_accounts(subject_id)'))
                db.session.execute(text('CREATE INDEX IF NOT EXISTS ix_social_accounts_platform ON social_accounts(platform)'))
                db.session.execute(text('CREATE INDEX IF NOT EXISTS ix_social_accounts_username ON social_accounts(username)'))
                db.session.commit()
                app.logger.info("Migration: created social_accounts table")
        except Exception as e:
            app.logger.warning(f"social_accounts migration note: {e}")
            db.session.rollback()

        # Migration: resize encrypted columns for PostgreSQL (SQLite ignores length)
        try:
            if db.engine.name == 'postgresql':
                from sqlalchemy import inspect, text
                encrypted_col_resizes = {
                    'clients': ['contact_person', 'contact_email', 'contact_phone',
                                'address_street', 'address_number', 'address_city',
                                'address_postal', 'address_country', 'social_security_number',
                                'vat_number', 'bank_account', 'contract_number',
                                'date_of_birth', 'place_of_birth'],
                    'subjects': ['date_of_birth', 'place_of_birth', 'nationality',
                                 'identification_number', 'phone', 'email',
                                 'license_plate', 'vin', 'insurance_company',
                                 'imo_number', 'mmsi', 'eni_number', 'vessel_nationality'],
                    'addresses': ['street', 'number', 'zipcode', 'town', 'country'],
                    'financial_records': ['counterparty_name', 'counterparty_account',
                                          'counterparty_bank', 'counterparty_country'],
                }
                for table, columns in encrypted_col_resizes.items():
                    inspector = inspect(db.engine)
                    col_info = {c['name']: c for c in inspector.get_columns(table)}
                    for col in columns:
                        if col in col_info:
                            col_type = str(col_info[col]['type'])
                            # Only resize varchar columns that are too small
                            if col_type.startswith('VARCHAR') and col_type != 'VARCHAR(500)':
                                db.session.execute(
                                    text(f'ALTER TABLE {table} ALTER COLUMN {col} TYPE VARCHAR(500)')
                                )
                                app.logger.info(f"Migration: resized {table}.{col} to VARCHAR(500)")
                db.session.commit()
        except Exception as e:
            app.logger.warning(f"Column resize migration note: {e}")
            db.session.rollback()

        # Initialize default settings
        from .models import Setting, init_default_settings
        init_default_settings()
        
        # Create default admin user if none exists
        if not User.query.filter_by(role='admin').first():
            admin = User(
                username='admin',
                email='admin@localhost',
                full_name='System Administrator',
                role='admin'
            )
            admin.set_password('changeme123')  # Must be changed on first login
            db.session.add(admin)
            db.session.commit()
            print("Default admin user created. Please change the password immediately.")
    
    return app


def init_db(app: Flask, database_url: str = None):
    """
    Configure database for CMS.
    
    Args:
        app: Flask application instance
        database_url: PostgreSQL connection string
    """
    if database_url:
        app.config['SQLALCHEMY_DATABASE_URI'] = database_url
    elif not app.config.get('SQLALCHEMY_DATABASE_URI'):
        # Default to PostgreSQL only if not already set
        db_host = app.config.get('DB_HOST', 'localhost')
        db_port = app.config.get('DB_PORT', '5432')
        db_name = app.config.get('DB_NAME', 'cms_db')
        db_user = app.config.get('DB_USER', 'postgres')
        db_pass = app.config.get('DB_PASSWORD', '')
        
        app.config['SQLALCHEMY_DATABASE_URI'] = (
            f'postgresql://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}'
        )
    
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'pool_size': 10,
        'pool_recycle': 3600,
        'pool_pre_ping': True
    }


__all__ = ['db', 'create_cms_module', 'init_db', 'models', 'routes', 'auth']
