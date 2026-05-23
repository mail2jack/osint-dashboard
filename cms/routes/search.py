import logging

from flask import request, jsonify, render_template
from flask_login import login_required, current_user

from . import cms_bp
from ..models import db, Case, Client, Subject, Finding, FinancialRecord, Comment, AuditLog

logger = logging.getLogger(__name__)


@cms_bp.route('/search')
@login_required
def search():
    """Global search across all entities with full page results."""
    query = request.args.get('q', '')
    entity_type = request.args.get('type', 'all')

    results = {
        'cases': [],
        'clients': [],
        'subjects': [],
        'findings': [],
        'financials': [],
        'comments': [],
        'notes': []
    }

    if query and len(query) >= 2:
        if entity_type in ['all', 'cases']:
            cases = Case.query.join(Client).filter(
                Case.is_deleted == False,
                db.or_(
                    Case.title.ilike(f'%{query}%'),
                    Case.case_number.ilike(f'%{query}%'),
                    Case.description.ilike(f'%{query}%')
                )
            ).limit(20).all()
            results['cases'] = [{
                'id': c.id,
                'title': c.title,
                'case_number': c.case_number,
                'status': c.status,
                'priority': c.priority,
                'client_name': c.client.name if c.client else None,
                'created_at': c.created_at.strftime('%Y-%m-%d') if c.created_at else None
            } for c in cases]

        if entity_type in ['all', 'clients']:
            clients = Client.query.filter(
                Client.is_deleted == False,
                Client.name.ilike(f'%{query}%')
            ).limit(20).all()
            results['clients'] = [{
                'id': c.id,
                'name': c.name,
                'contact_person': c.contact_person,
                'is_company': c.is_company,
                'is_active': c.is_active,
                'contract_number': c.contract_number
            } for c in clients]

        if entity_type in ['all', 'subjects']:
            subjects = Subject.query.filter(
                Subject.is_deleted == False,
                db.or_(
                    Subject.name.ilike(f'%{query}%'),
                    Subject.identification_number.ilike(f'%{query}%')
                )
            ).limit(20).all()
            results['subjects'] = [{
                'id': s.id,
                'name': s.name,
                'subject_type': s.subject_type,
                'risk_score': s.risk_score,
                'created_at': s.created_at.strftime('%Y-%m-%d') if s.created_at else None
            } for s in subjects]

        if entity_type in ['all', 'findings']:
            findings = Finding.query.join(Case).filter(
                Finding.is_deleted == False,
                db.or_(
                    Finding.title.ilike(f'%{query}%'),
                    Finding.content.ilike(f'%{query}%')
                )
            ).limit(20).all()
            results['findings'] = [{
                'id': f.id,
                'title': f.title,
                'case_id': f.case_id,
                'case_number': f.case.case_number if f.case else None,
                'finding_type': f.finding_type,
                'source_type': f.source_type,
                'created_at': f.created_at.strftime('%Y-%m-%d') if f.created_at else None
            } for f in findings]

        if entity_type in ['all', 'financials']:
            financials = FinancialRecord.query.join(Case).filter(
                FinancialRecord.is_deleted == False,
                db.or_(
                    FinancialRecord.description.ilike(f'%{query}%'),
                    FinancialRecord.source_reference.ilike(f'%{query}%')
                )
            ).limit(20).all()
            results['financials'] = [{
                'id': f.id,
                'amount': float(f.amount) if f.amount else 0,
                'currency': f.currency or 'EUR',
                'case_id': f.case_id,
                'case_number': f.case.case_number if f.case else None,
                'transaction_type': f.transaction_type,
                'transaction_date': f.transaction_date.strftime('%Y-%m-%d') if f.transaction_date else '',
                'description': f.description[:100] if f.description else None
            } for f in financials]

        if entity_type in ['all', 'comments']:
            comments = Comment.query.filter(
                Comment.is_deleted == False,
                Comment.content.ilike(f'%{query}%')
            ).limit(20).all()
            results['comments'] = []
            for c in comments:
                _case = db.session.get(Case, c.case_id) if c.case_id else None
                results['comments'].append({
                    'id': c.id,
                    'content': (c.content[:200] + '...') if c.content and len(c.content) > 200 else (c.content or ''),
                    'comment_type': c.comment_type,
                    'case_id': c.case_id,
                    'subject_id': c.subject_id,
                    'client_id': c.client_id,
                    'case_number': _case.case_number if _case else None,
                    'author_name': c.author.full_name if c.author else 'Unknown',
                    'created_at': c.created_at.strftime('%Y-%m-%d') if c.created_at else None
                })

        if entity_type in ['all', 'notes']:
            subject_notes = Subject.query.filter(
                Subject.is_deleted == False,
                Subject.notes.ilike(f'%{query}%')
            ).limit(10).all()
            results['notes'] = [{
                'id': s.id,
                'name': s.name,
                'subject_type': s.subject_type,
                'note_preview': s.notes[:150] + ('...' if len(s.notes) > 150 else '') if s.notes else None,
                'entity_type': 'subject'
            } for s in subject_notes]
            comment_results = Comment.query.filter(
                Comment.is_deleted == False,
                Comment.subject_id.isnot(None),
                Comment.content.ilike(f'%{query}%')
            ).order_by(Comment.created_at.desc()).limit(10).all()
            for c in comment_results:
                sub = db.session.get(Subject, c.subject_id)
                if sub and not sub.is_deleted:
                    results['notes'].append({
                        'id': sub.id,
                        'name': sub.name + f' (comment: {c.comment_type})',
                        'subject_type': sub.subject_type,
                        'note_preview': c.content[:150] + ('...' if len(c.content) > 150 else '') if c.content else None,
                        'entity_type': 'subject',
                        'comment_date': c.created_at.isoformat() if c.created_at else None
                    })

        AuditLog.log(
            user_id=current_user.id,
            action='search',
            entity_type='global_search',
            ip_address=request.remote_addr,
            description=f"Searched for: {query}"
        )
        db.session.commit()

    return render_template('cms/search.html',
                           query=query,
                           results=results,
                           active_filter=entity_type
                           )


@cms_bp.route('/api/search')
@login_required
def api_search():
    """API endpoint for autocomplete/typeahead search."""
    query = request.args.get('q', '')
    entity_type = request.args.get('type', '')

    if not query or len(query) < 2:
        return jsonify({'results': []})

    results = {'cases': [], 'clients': [], 'subjects': []}

    if not entity_type or entity_type == 'cases':
        cases = Case.query.filter(
            Case.is_deleted == False,
            db.or_(
                Case.title.ilike(f'%{query}%'),
                Case.case_number.ilike(f'%{query}%')
            )
        ).limit(5).all()
        results['cases'] = [{
            'id': c.id,
            'title': c.title,
            'case_number': c.case_number,
            'type': 'case'
        } for c in cases]

    if not entity_type or entity_type == 'clients':
        clients = Client.query.filter(
            Client.is_deleted == False,
            Client.name.ilike(f'%{query}%')
        ).limit(5).all()
        results['clients'] = [{
            'id': c.id,
            'name': c.name,
            'type': 'client'
        } for c in clients]

    if not entity_type or entity_type == 'subjects':
        subjects = Subject.query.filter(
            Subject.is_deleted == False,
            Subject.name.ilike(f'%{query}%')
        ).limit(5).all()
        results['subjects'] = [{
            'id': s.id,
            'name': s.name,
            'type': 'subject',
            'subject_type': s.subject_type
        } for s in subjects]

    return jsonify({'results': results})
