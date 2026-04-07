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
    
    # Create tables if they don't exist
    with app.app_context():
        db.create_all()
        
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
