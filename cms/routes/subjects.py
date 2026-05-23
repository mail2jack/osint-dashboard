import json
import logging
import os
import math
from datetime import datetime, timezone

from flask import (
    request, jsonify, render_template,
    redirect, url_for, flash, current_app, abort
)
from flask_login import login_required, current_user

from . import cms_bp
from ..models import (
    db, Subject, Case, Address, Contact, AuditLog, User, Screenshot,
    subject_relations, Finding, SocialAccount
)
from ..auth import roles_required, senior_required
from ..encryption_utils import encryptor
from .utils import (
    normalize_phone, find_similar_subjects, find_similar_clients,
    check_for_exact_match, normalize_name
)

logger = logging.getLogger(__name__)


@cms_bp.route('/subjects')
@login_required
def subjects():
    """List all subjects with search, filtering, and sorting."""
    page = request.args.get('page', 1, type=int)
    per_page = 30
    search = request.args.get('search', '')
    subject_type = request.args.get('type', '')
    sort = request.args.get('sort', 'name')
    order = request.args.get('order', 'asc')
    fmt = request.args.get('format', '')

    query = Subject.query.filter_by(is_deleted=False)

    if search:
        query = query.outerjoin(SocialAccount, SocialAccount.subject_id == Subject.id).filter(
            db.or_(
                Subject.name.ilike(f'%{search}%'),
                SocialAccount.username.ilike(f'%{search}%'),
            )
        ).distinct()

    if subject_type:
        query = query.filter_by(subject_type=subject_type)

    # Sorting
    sort_columns = {
        'name': Subject.name,
        'type': Subject.subject_type,
        'risk': Subject.risk_score,
    }

    sort_col = sort_columns.get(sort, Subject.name)
    if order == 'desc':
        sort_col = sort_col.desc()

    # JSON format for API calls
    if fmt == 'json':
        subjects_list = query.order_by(sort_col).all()
        return jsonify({
            'subjects': [{'id': s.id, 'name': s.name, 'type': s.subject_type} for s in subjects_list]
        })

    pagination = query.order_by(sort_col).paginate(
        page=page, per_page=per_page, error_out=False
    )

    return render_template('cms/subjects/list.html',
                           subjects=pagination.items,
                           pagination=pagination,
                           filters={'search': search, 'type': subject_type,
                                    'sort': sort, 'order': order}
                           )


@cms_bp.route('/subjects/<subject_id>')
@login_required
def view_subject(subject_id: str):
    """View subject details."""
    subject = db.session.get(Subject, subject_id) or abort(404)
    subject.decrypt_identifiers()
    # Parse vessel_data to dict for template (SQLite JSON column may return string)
    vd = subject.vessel_data
    while isinstance(vd, str):
        try:
            vd = json.loads(vd)
        except (json.JSONDecodeError, TypeError):
            try:
                import ast
                vd = ast.literal_eval(vd)
            except (ValueError, SyntaxError, TypeError):
                vd = {}
    subject.vessel_data = vd if isinstance(vd, dict) else {}
    for addr in subject.addresses:
        addr.decrypt_fields()
    for c in subject.contacts:
        c.decrypt_fields()

    financials = subject.financial_records.filter_by(is_deleted=False).all()
    findings = subject.findings.filter_by(
        is_deleted=False).order_by(Finding.created_at.desc()).all()

    # Get linked cases
    linked_cases = []
    first_case_id = None
    for case in Case.query.filter_by(is_deleted=False).all():
        if subject in case.subjects.all():
            case_info = {'id': case.id,
                         'case_number': case.case_number, 'title': case.title}
            linked_cases.append(case_info)
            if first_case_id is None:
                first_case_id = case.id

    AuditLog.log(
        user_id=current_user.id,
        action='read',
        entity_type='subject',
        entity_id=subject_id,
        ip_address=request.remote_addr,
        description=f"Viewed subject: {subject.name}"
    )
    db.session.commit()

    return render_template('cms/subjects/view.html',
                           subject=subject,
                           financials=financials,
                           findings=findings,
                           linked_cases=linked_cases,
                           first_case_id=first_case_id
                           )


@cms_bp.route('/api/check-duplicate')
@login_required
def check_duplicate():
    """Check for duplicate subjects or clients by name (for real-time lookup)."""
    name = request.args.get('name', '').strip()
    entity_type = request.args.get('type', 'subject')  # 'subject' or 'client'

    if len(name) < 2:
        return jsonify({'duplicates': [], 'exact': None})

    if entity_type == 'subject':
        exact = check_for_exact_match(name, 'subject')
        similar = find_similar_subjects(name)[:5]
        return jsonify({'duplicates': similar, 'exact': exact})
    else:
        exact = check_for_exact_match(name, 'client')
        similar = find_similar_clients(name)[:5]
        return jsonify({'duplicates': similar, 'exact': exact})


@cms_bp.route('/subjects/create', methods=['GET', 'POST'])
@login_required
@roles_required('admin', 'senior_investigator', 'junior_investigator')
def create_subject():
    """Create a new subject with duplicate detection."""
    if request.method == 'POST':
        data = request.get_json() if request.is_json else request.form

        required = ['name', 'subject_type']
        for field in required:
            if not data.get(field):
                return jsonify({'error': f'{field} is required'}), 400

        name = data['name'].strip()

        # Check for duplicates
        exact_match = check_for_exact_match(name, 'subject')
        similar = find_similar_subjects(name)

        # Skip duplicate check if already confirmed
        if not data.get('confirm_duplicate'):
            if exact_match:
                if request.is_json:
                    return jsonify({
                        'error': 'exact_match',
                        'message': f'A subject with this name already exists: {exact_match["name"]}',
                        'duplicate': exact_match,
                        'similar': similar[:5]
                    }), 409
                flash(
                    f'Warning: A subject with this name already exists: {exact_match["name"]}', 'warning')
                case_id = request.args.get('case_id')
                return render_template('cms/subjects/create.html',
                                       case_id=case_id,
                                       duplicate_warning=True,
                                       exact_match=exact_match,
                                       similar_subjects=similar[:5],
                                       submitted_name=name,
                                       submitted_type=data.get('subject_type'))

            if similar and not request.is_json:
                flash(
                    'Warning: Similar subjects found. Please review before creating.', 'warning')
                case_id = request.args.get('case_id')
                return render_template('cms/subjects/create.html',
                                       case_id=case_id,
                                       duplicate_warning=True,
                                       similar_subjects=similar[:5],
                                       submitted_name=name,
                                       submitted_type=data.get('subject_type'))

        subject = Subject(
            name=name,
            subject_type=data['subject_type'],
            risk_score=data.get('risk_score', 0),
            risk_factors=data.get('risk_factors'),
            notes=data.get('notes'),
            registration_number=data.get('registration_number'),
            legal_form=data.get('legal_form'),
            asset_type=data.get('asset_type'),
            estimated_value=data.get('estimated_value'),
            currency=data.get('currency', 'EUR'),
            license_plate=data.get('license_plate'),
            vin=data.get('vin'),
            insurance_company=data.get('insurance_company'),
            brand=data.get('brand'),
            vehicle_type=data.get('vehicle_type'),
            imo_number=data.get('imo_number'),
            mmsi=data.get('mmsi'),
            eni_number=data.get('eni_number'),
            vessel_nationality=data.get('vessel_nationality'),
            date_of_birth=data.get('date_of_birth'),
            place_of_birth=data.get('place_of_birth'),
            identification_number=data.get('identification_number')
        )

        if data['subject_type'] == 'vehicle':
            rdw_data = {}
            rdw_fields = [
                'handelsbenaming', 'voertuigsoort', 'eerste_kleur', 'tweede_kleur',
                'aantal_deuren', 'aantal_zitplaatsen', 'cilinderinhoud', 'aantal_cilinders',
                'massa_ledig', 'maximum_massa', 'vervaldatum_apk', 'wam_verzekerd',
                'taxi_indicator', 'export_indicator', 'europese_voertuigcategorie',
                'zuinigheidsclassificatie', 'catalogusprijs', 'datum_eerste_toelating',
                'type', 'variant', 'uitvoering', 'typegoedkeuringsnummer', 'wielbasis'
            ]
            for field in rdw_fields:
                if data.get(field):
                    rdw_data[field] = data.get(field)

            if rdw_data or data.get('license_plate'):
                rdw_data['kenteken'] = data.get('license_plate', '').upper()
                rdw_data['merk'] = data.get('brand', '')
                rdw_data['inrichting'] = data.get('vehicle_type', '')
                if data.get('eerste_kleur'):
                    rdw_data['kleur'] = data.get('eerste_kleur')
                subject.rdw_data = rdw_data

        if data['subject_type'] == 'vessel' and data.get('vessel_data'):
            try:
                subject.vessel_data = json.loads(data['vessel_data'])
            except (json.JSONDecodeError, TypeError):
                subject.vessel_data = data['vessel_data']

        # Encrypt all identifying fields (person + vehicle + vessel)
        subject.encrypt_identifiers()

        db.session.add(subject)
        db.session.flush()  # Get subject ID before adding addresses

        # Handle structured addresses
        if data.get('addresses_data'):
            try:
                addresses_data = json.loads(data['addresses_data']) if isinstance(
                    data['addresses_data'], str) else data['addresses_data']
                for addr_data in addresses_data:
                    if addr_data.get('street') or addr_data.get('zipcode'):
                        address = Address(
                            subject_id=subject.id,
                            street=addr_data.get('street'),
                            number=addr_data.get('number'),
                            zipcode=addr_data.get('zipcode'),
                            town=addr_data.get('town'),
                            country=addr_data.get('country', 'Netherlands'),
                            is_primary=addr_data.get('is_primary', False)
                        )
                        address.encrypt_fields()
                        db.session.add(address)
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(f"Failed to parse addresses_data: {e}")

        # Handle structured contacts
        if data.get('contacts_data'):
            try:
                contacts_data = json.loads(data['contacts_data']) if isinstance(
                    data['contacts_data'], str) else data['contacts_data']
                for c_data in contacts_data:
                    if c_data.get('value'):
                        contact = Contact(
                            subject_id=subject.id,
                            contact_type=c_data.get('contact_type', 'email'),
                            value=c_data.get('value'),
                            is_primary=c_data.get('is_primary', False)
                        )
                        contact.encrypt_fields()
                        db.session.add(contact)
                        # Also set legacy fields for backward compat
                        if c_data.get('contact_type') == 'email' and c_data.get('is_primary'):
                            subject.email = encryptor.encrypt(c_data.get(
                                'value')) if c_data.get('value') else None
                        elif c_data.get('contact_type') == 'phone' and c_data.get('is_primary'):
                            subject.phone = encryptor.encrypt(normalize_phone(
                                c_data.get('value'))) if c_data.get('value') else None
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(f"Failed to parse contacts_data: {e}")

        # Link to case if specified
        if data.get('case_id'):
            case = db.session.get(Case, data['case_id'])
            if case:
                case.subjects.append(subject)

        AuditLog.log(
            user_id=current_user.id,
            action='create',
            entity_type='subject',
            entity_id=subject.id,
            new_values={'name': subject.name, 'type': subject.subject_type},
            ip_address=request.remote_addr,
            case_id=data.get('case_id'),
            description=f"Created subject: {subject.name}"
        )
        db.session.commit()

        if request.is_json:
            return jsonify({'message': 'Subject created', 'subject': subject.to_dict()}), 201

        flash(f'Subject {subject.name} created successfully.', 'success')

        # If created from case view, redirect back to case
        if data.get('case_id'):
            return redirect(url_for('cms.view_case', case_id=data['case_id']))

        return redirect(url_for('cms.view_subject', subject_id=subject.id))

    # Pass case_id from query param if coming from case view
    case_id = request.args.get('case_id')
    return render_template('cms/subjects/create.html', case_id=case_id)


@cms_bp.route('/subjects/<subject_id>/edit', methods=['GET', 'POST'])
@login_required
@roles_required('admin', 'senior_investigator', 'junior_investigator')
def edit_subject(subject_id: str):
    """Edit subject details."""
    subject = db.session.get(Subject, subject_id) or abort(404)

    if request.method == 'POST':
        data = request.get_json() if request.is_json else request.form
        changes = {}

        # Update basic fields
        if data.get('name') and data['name'] != subject.name:
            changes['name'] = {'old': subject.name, 'new': data['name']}
            subject.name = data['name']

        # Update subject_type if provided
        if 'subject_type' in data and data['subject_type'] and data['subject_type'] != subject.subject_type:
            changes['subject_type'] = {
                'old': subject.subject_type, 'new': data['subject_type']}
            subject.subject_type = data['subject_type']

        if 'risk_score' in data:
            changes['risk_score'] = {
                'old': subject.risk_score, 'new': data['risk_score']}
            subject.risk_score = int(data['risk_score'])

        if 'notes' in data:
            subject.notes = data['notes']

        # Update encrypted fields for persons
        encrypted_fields = ['date_of_birth', 'place_of_birth', 'nationality',
                            'identification_number', 'address', 'phone', 'email']
        for field in encrypted_fields:
            if field in data:
                new_value = data[field] if data[field] else None
                old_value = getattr(subject, field)
                # Decrypt old value for comparison
                try:
                    if old_value:
                        old_value = encryptor.decrypt(old_value)
                except Exception:
                    pass  # Value may already be plaintext or encryption key may have changed
                if field == 'phone' and new_value:
                    new_value = normalize_phone(new_value)
                if new_value != old_value:
                    changes[field] = {
                        'old': old_value or '[empty]', 'new': new_value or '[empty]'}
                    if new_value:
                        setattr(subject, field, encryptor.encrypt(new_value))
                    else:
                        setattr(subject, field, None)

        # Update vehicle fields
        # Encrypted vehicle fields
        encrypted_vehicle_fields = [
            'license_plate', 'vin', 'insurance_company']
        for field in encrypted_vehicle_fields:
            if field in data:
                new_value = data[field] if data[field] else None
                old_value = getattr(subject, field)
                # Decrypt old value for comparison
                try:
                    if old_value:
                        old_value = encryptor.decrypt(old_value)
                except Exception:
                    pass  # Value may already be plaintext or encryption key may have changed
                if new_value != old_value:
                    changes[field] = {
                        'old': old_value or '[empty]', 'new': new_value or '[empty]'}
                    if new_value:
                        setattr(subject, field, encryptor.encrypt(new_value))
                    else:
                        setattr(subject, field, None)

        # Non-encrypted vehicle fields
        non_encrypted_vehicle_fields = ['brand', 'vehicle_type']
        for field in non_encrypted_vehicle_fields:
            if field in data:
                new_value = data[field] if data[field] else None
                if new_value != getattr(subject, field):
                    changes[field] = {'old': getattr(
                        subject, field) or '[empty]', 'new': new_value or '[empty]'}
                    setattr(subject, field, new_value)

        # Encrypted vessel fields
        encrypted_vessel_fields = ['imo_number',
                                   'mmsi', 'eni_number', 'vessel_nationality']
        for field in encrypted_vessel_fields:
            if field in data:
                new_value = data[field] if data[field] else None
                old_value = getattr(subject, field)
                try:
                    if old_value:
                        old_value = encryptor.decrypt(old_value)
                except Exception:
                    pass  # Value may already be plaintext or encryption key may have changed
                if new_value != old_value:
                    changes[field] = {
                        'old': old_value or '[empty]', 'new': new_value or '[empty]'}
                    if new_value:
                        setattr(subject, field, encryptor.encrypt(new_value))
                    else:
                        setattr(subject, field, None)

        # Handle structured addresses
        if data.get('addresses_data'):
            try:
                addresses_data = json.loads(data['addresses_data']) if isinstance(
                    data['addresses_data'], str) else data['addresses_data']
                old_addresses = list(subject.addresses)
                addr_count_before = len(old_addresses)
                for addr in old_addresses:
                    db.session.delete(addr)
                for addr_data in addresses_data:
                    if addr_data.get('street') or addr_data.get('zipcode'):
                        address = Address(
                            subject_id=subject.id,
                            street=addr_data.get('street'),
                            number=addr_data.get('number'),
                            zipcode=addr_data.get('zipcode'),
                            town=addr_data.get('town'),
                            country=addr_data.get('country', 'Netherlands'),
                            is_primary=addr_data.get('is_primary', False)
                        )
                        address.encrypt_fields()
                        db.session.add(address)
                changes['addresses'] = {
                    'old': f'{addr_count_before} address(es)', 'new': f'{len(addresses_data)} address(es)'}
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(f"Failed to parse addresses_data: {e}")

        # Handle structured contacts
        if data.get('contacts_data'):
            try:
                contacts_data = json.loads(data['contacts_data']) if isinstance(
                    data['contacts_data'], str) else data['contacts_data']
                old_contacts = list(subject.contacts)
                contact_count_before = len(old_contacts)
                for c in old_contacts:
                    db.session.delete(c)
                for c_data in contacts_data:
                    if c_data.get('value'):
                        contact = Contact(
                            subject_id=subject.id,
                            contact_type=c_data.get('contact_type', 'email'),
                            value=c_data.get('value'),
                            is_primary=c_data.get('is_primary', False)
                        )
                        contact.encrypt_fields()
                        db.session.add(contact)
                        # Update legacy fields for backward compat
                        if c_data.get('contact_type') == 'email' and c_data.get('is_primary'):
                            try:
                                current = encryptor.decrypt(
                                    subject.email) if subject.email else None
                            except Exception:
                                current = subject.email  # may already be plaintext
                            if c_data.get('value') != current:
                                subject.email = encryptor.encrypt(c_data.get(
                                    'value')) if c_data.get('value') else None
                        elif c_data.get('contact_type') == 'phone' and c_data.get('is_primary'):
                            try:
                                current = encryptor.decrypt(
                                    subject.phone) if subject.phone else None
                            except Exception:
                                current = subject.phone  # may already be plaintext
                            new_val = normalize_phone(c_data.get('value'))
                            if new_val != current:
                                subject.phone = encryptor.encrypt(
                                    new_val) if new_val else None
                changes['contacts'] = {
                    'old': f'{contact_count_before} contact(s)', 'new': f'{len(contacts_data)} contact(s)'}
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(f"Failed to parse contacts_data: {e}")

        # Update RDW data if provided
        rdw_fields = [
            'handelsbenaming', 'voertuigsoort', 'eerste_kleur', 'tweede_kleur',
            'aantal_deuren', 'aantal_zitplaatsen', 'cilinderinhoud', 'aantal_cilinders',
            'massa_ledig', 'maximum_massa', 'vervaldatum_apk', 'wam_verzekerd',
            'taxi_indicator', 'export_indicator', 'europese_voertuigcategorie',
            'zuinigheidsclassificatie', 'catalogusprijs', 'datum_eerste_toelating',
            'type', 'variant', 'uitvoering', 'typegoedkeuringsnummer', 'wielbasis'
        ]

        rdw_data = {}
        for field in rdw_fields:
            if data.get(field):
                rdw_data[field] = data[field]

        # Also store basic vehicle fields in RDW data
        if data.get('license_plate'):
            rdw_data['kenteken'] = data['license_plate']
        if data.get('brand'):
            rdw_data['merk'] = data['brand']
        if data.get('vehicle_type'):
            rdw_data['inrichting'] = data['vehicle_type']
        if data.get('vin'):
            rdw_data['chassisnummer'] = data['vin']

        if rdw_data:
            existing_rdw = subject.rdw_data or {}
            existing_rdw.update(rdw_data)
            subject.rdw_data = existing_rdw
            changes['rdw_data'] = {
                'old': 'updated', 'new': 'RDW fields updated'}

        # Update vessel data if provided
        if data.get('vessel_data'):
            try:
                subject.vessel_data = json.loads(data['vessel_data'])
            except (json.JSONDecodeError, TypeError):
                subject.vessel_data = data['vessel_data']
            changes['vessel_data'] = {
                'old': 'updated', 'new': 'Vessel data updated'}

        subject.updated_at = datetime.now(timezone.utc)

        AuditLog.log(
            user_id=current_user.id,
            action='update',
            entity_type='subject',
            entity_id=subject_id,
            changes=changes,
            ip_address=request.remote_addr,
            description=f"Updated subject: {subject.name}"
        )
        db.session.commit()

        if request.is_json:
            return jsonify({'message': 'Subject updated', 'subject': subject.to_dict()})

        flash('Subject updated successfully.', 'success')
        return redirect(url_for('cms.view_subject', subject_id=subject.id))

    subject.decrypt_identifiers()
    for addr in subject.addresses:
        addr.decrypt_fields()
    for c in subject.contacts:
        c.decrypt_fields()
    return render_template('cms/subjects/edit.html', subject=subject)


@cms_bp.route('/subjects/<subject_id>/photo', methods=['POST'])
@login_required
@roles_required('admin', 'senior_investigator', 'junior_investigator')
def upload_subject_photo(subject_id: str):
    """Upload a photo for a subject."""
    subject = db.session.get(Subject, subject_id) or abort(404)

    if 'photo' not in request.files:
        return jsonify({'error': 'No photo provided'}), 400

    file = request.files['photo']

    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    # Only allow images
    allowed_extensions = {'jpg', 'jpeg', 'png', 'gif', 'webp'}
    ext = file.filename.rsplit(
        '.', 1)[-1].lower() if '.' in file.filename else ''
    if ext not in allowed_extensions:
        return jsonify({'error': 'Only image files allowed'}), 400

    # Create upload directory
    upload_dir = os.path.join(current_app.root_path,
                              'static', 'uploads', 'subjects', subject_id)
    os.makedirs(upload_dir, exist_ok=True)

    # Remove old photo if exists
    if subject.photo_path:
        old_path = os.path.join(current_app.root_path,
                                'static', subject.photo_path.lstrip('/'))
        if os.path.exists(old_path):
            os.remove(old_path)

    # Save new photo
    filename = f"photo.{ext}"
    file_path = os.path.join(upload_dir, filename)
    file.save(file_path)

    # Update subject
    subject.photo_path = f"/uploads/subjects/{subject_id}/{filename}"

    AuditLog.log(
        user_id=current_user.id,
        action='update',
        entity_type='subject',
        entity_id=subject_id,
        changes={'photo': 'uploaded'},
        ip_address=request.remote_addr,
        description=f"Uploaded photo for {subject.name}"
    )
    db.session.commit()

    return jsonify({
        'message': 'Photo uploaded',
        'photo_path': subject.photo_path
    })


@cms_bp.route('/subjects/<subject_id>/face-encoding', methods=['POST'])
@login_required
@roles_required('admin', 'senior_investigator', 'junior_investigator')
def save_face_encoding(subject_id: str):
    """Save face encoding for a subject."""
    subject = db.session.get(Subject, subject_id) or abort(404)
    data = request.get_json()

    if not data or 'encoding' not in data:
        return jsonify({'error': 'No encoding provided'}), 400

    encoding = data['encoding']

    if not isinstance(encoding, list) or len(encoding) != 128:
        return jsonify({'error': 'Invalid encoding format'}), 400

    subject.face_encoding = encoding

    AuditLog.log(
        user_id=current_user.id,
        action='face_encoding_saved',
        entity_type='subject',
        entity_id=subject_id,
        ip_address=request.remote_addr,
        description=f"Saved face encoding for {subject.name}"
    )
    db.session.commit()

    return jsonify({
        'message': 'Face encoding saved',
        'has_encoding': True
    })


@cms_bp.route('/subjects/<subject_id>/face-encoding', methods=['DELETE'])
@login_required
@roles_required('admin', 'senior_investigator', 'junior_investigator')
def delete_face_encoding(subject_id: str):
    """Delete face encoding for a subject."""
    subject = db.session.get(Subject, subject_id) or abort(404)

    subject.face_encoding = None

    AuditLog.log(
        user_id=current_user.id,
        action='face_encoding_deleted',
        entity_type='subject',
        entity_id=subject_id,
        ip_address=request.remote_addr,
        description=f"Deleted face encoding for {subject.name}"
    )
    db.session.commit()

    return jsonify({
        'message': 'Face encoding deleted',
        'has_encoding': False
    })


@cms_bp.route('/subjects/compare-faces', methods=['POST'])
@login_required
def compare_faces():
    """Compare face encodings. Returns list of matching subjects."""
    data = request.get_json()

    if not data or 'encoding' not in data:
        return jsonify({'error': 'No encoding provided'}), 400

    target_encoding = data['encoding']

    if not isinstance(target_encoding, list) or len(target_encoding) != 128:
        return jsonify({'error': 'Invalid encoding format'}), 400

    threshold = data.get('threshold', 0.6)
    limit = data.get('limit', 20)

    subjects_with_faces = Subject.query.filter(
        Subject.face_encoding.isnot(None),
        Subject.is_deleted == False,
        Subject.photo_path.isnot(None)
    ).all()

    def euclidean_distance(enc1, enc2):
        return math.sqrt(sum((a - b) ** 2 for a, b in zip(enc1, enc2)))

    matches = []
    for subject in subjects_with_faces:
        distance = euclidean_distance(target_encoding, subject.face_encoding)
        if distance < threshold:
            matches.append({
                'id': subject.id,
                'name': subject.name,
                'subject_type': subject.subject_type,
                'photo_path': subject.photo_path,
                'distance': round(distance, 4),
                'similarity': round((1 - distance) * 100, 1)
            })

    matches.sort(key=lambda x: x['distance'])
    matches = matches[:limit]

    return jsonify({
        'matches': matches,
        'total_searched': len(subjects_with_faces),
        'threshold': threshold
    })


@cms_bp.route('/api/subjects/with-faces', methods=['GET'])
@login_required
def get_subjects_with_faces():
    """Get list of subjects with face encodings for face-api.js matching."""
    subjects = Subject.query.filter(
        Subject.face_encoding.isnot(None),
        Subject.is_deleted == False,
        Subject.photo_path.isnot(None)
    ).all()

    return jsonify({
        'subjects': [{
            'id': s.id,
            'name': s.name,
            'photo_path': s.photo_path,
            'face_encoding': s.face_encoding
        } for s in subjects]
    })


@cms_bp.route('/subjects/<subject_id>/relationships')
@login_required
def get_subject_relationships(subject_id: str):
    """Get relationship network data for a subject."""
    try:
        subject = db.session.get(Subject, subject_id) or abort(404)

        # Get ALL relationships for this subject (both directions now)
        related_rows = db.session.execute(
            subject_relations.select().where(subject_relations.c.subject_id == subject.id)
        ).fetchall()

        # Build a map of related subjects
        related_ids = [row.related_subject_id for row in related_rows]
        related = Subject.query.filter(Subject.id.in_(
            related_ids), Subject.is_deleted == False).all() if related_ids else []

        # Build nodes and edges for visualization
        nodes = [{
            'id': subject.id,
            'name': subject.name,
            'type': subject.subject_type,
            'isMain': True
        }]

        edges = []
        edge_ids = set()  # Use sorted IDs to avoid duplicates

        # Helper to get sorted edge ID
        def sorted_edge_id(a, b):
            return f"{min(a, b)}-{max(a, b)}"

        for rel in related:
            nodes.append({
                'id': rel.id,
                'name': rel.name,
                'type': rel.subject_type,
                'isMain': False
            })

            # Get relationship type from either direction
            rel_type = 'related'
            type_rows = db.session.execute(
                subject_relations.select().where(
                    (subject_relations.c.subject_id == subject.id) &
                    (subject_relations.c.related_subject_id == rel.id)
                )
            ).fetchall()
            if not type_rows:
                # Check reverse direction
                type_rows = db.session.execute(
                    subject_relations.select().where(
                        (subject_relations.c.subject_id == rel.id) &
                        (subject_relations.c.related_subject_id == subject.id)
                    )
                ).fetchall()
            if type_rows:
                rel_type = type_rows[0].relationship_type or 'related'

            edge_id = sorted_edge_id(subject.id, rel.id)
            if edge_id not in edge_ids:
                edges.append({
                    'id': edge_id,
                    'source': subject.id,
                    'target': rel.id,
                    'type': rel_type
                })
                edge_ids.add(edge_id)

        # Get second-degree connections (friends of friends)
        for rel in related:
            second_degree_rows = db.session.execute(
                subject_relations.select().where(subject_relations.c.subject_id == rel.id)
            ).fetchall()

            second_degree_ids = [
                row.related_subject_id for row in second_degree_rows if row.related_subject_id != subject.id]
            rel_related = Subject.query.filter(
                Subject.id.in_(second_degree_ids),
                Subject.is_deleted == False,
                Subject.id != subject.id
            ).all() if second_degree_ids else []

            for rr in rel_related:
                # Check if node already exists
                if not any(n['id'] == rr.id for n in nodes):
                    nodes.append({
                        'id': rr.id,
                        'name': rr.name,
                        'type': rr.subject_type,
                        'isMain': False
                    })

                edge_id = sorted_edge_id(rel.id, rr.id)
                if edge_id not in edge_ids:
                    rel_type = 'connected'
                    type_rows = db.session.execute(
                        subject_relations.select().where(
                            (subject_relations.c.subject_id == rel.id) &
                            (subject_relations.c.related_subject_id == rr.id)
                        )
                    ).fetchall()
                    if not type_rows:
                        # Check reverse direction
                        type_rows = db.session.execute(
                            subject_relations.select().where(
                                (subject_relations.c.subject_id == rr.id) &
                                (subject_relations.c.related_subject_id == rel.id)
                            )
                        ).fetchall()
                    if type_rows:
                        rel_type = type_rows[0].relationship_type or 'connected'

                    edges.append({
                        'id': edge_id,
                        'source': rel.id,
                        'target': rr.id,
                        'type': rel_type
                    })
                    edge_ids.add(edge_id)

        return jsonify({
            'subject': {
                'id': subject.id,
                'name': subject.name,
                'type': subject.subject_type
            },
            'nodes': nodes,
            'edges': edges
        })
    except Exception as e:
        logger.error(f"Error in get_subject_relationships: {str(e)} ({type(e).__name__})")
        return jsonify({'error': str(e), 'error_type': type(e).__name__}), 500


@cms_bp.route('/subjects/<subject_id>/add-relationship', methods=['POST'])
@login_required
@roles_required('admin', 'senior_investigator', 'junior_investigator')
def add_subject_relationship(subject_id: str):
    """Add a bidirectional relationship between two subjects."""
    try:
        subject = db.session.get(Subject, subject_id) or abort(404)
        data = request.get_json()
        if data is None:
            return jsonify({'error': 'Invalid JSON'}), 400

        related_id = data.get('related_subject_id')
        relationship_type = data.get('relationship_type', 'related')

        if not related_id:
            return jsonify({'error': 'Related subject ID required'}), 400

        if related_id == subject_id:
            return jsonify({'error': 'Cannot create relationship with self'}), 400

        related = db.session.get(Subject, related_id)
        if not related:
            return jsonify({'error': 'Related subject not found'}), 404

        existing_a = db.session.execute(
            subject_relations.select().where(
                (subject_relations.c.subject_id == subject.id) &
                (subject_relations.c.related_subject_id == related_id)
            )
        ).first()

        existing_b = db.session.execute(
            subject_relations.select().where(
                (subject_relations.c.subject_id == related_id) &
                (subject_relations.c.related_subject_id == subject.id)
            )
        ).first()

        if existing_a or existing_b:
            return jsonify({'error': 'Relationship already exists'}), 400

        db.session.execute(
            subject_relations.insert().values(
                subject_id=subject.id,
                related_subject_id=related_id,
                relationship_type=relationship_type
            )
        )
        db.session.execute(
            subject_relations.insert().values(
                subject_id=related_id,
                related_subject_id=subject.id,
                relationship_type=relationship_type
            )
        )

        AuditLog.log(
            user_id=current_user.id,
            action='create',
            entity_type='subject_relation',
            entity_id=f"{subject.id}-{related_id}",
            ip_address=request.remote_addr,
            description=f"Added bidirectional {relationship_type} relationship between {subject.name} and {related.name}"
        )
        db.session.commit()

        return jsonify({
            'message': 'Relationship added',
            'relationship': {
                'subject_id': subject.id,
                'related_subject_id': related_id,
                'type': relationship_type,
                'bidirectional': True
            }
        })
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error adding relationship: {e} ({type(e).__name__})")
        return jsonify({'error': str(e)}), 500


@cms_bp.route('/subjects/<subject_id>/remove-relationship', methods=['POST'])
@login_required
@roles_required('admin', 'senior_investigator')
def remove_subject_relationship(subject_id: str):
    """Remove a relationship between two subjects."""
    try:
        subject = db.session.get(Subject, subject_id) or abort(404)
        data = request.get_json()
        if data is None:
            return jsonify({'error': 'Invalid JSON'}), 400

        related_id = data.get('related_subject_id')

        if not related_id:
            return jsonify({'error': 'Related subject ID required'}), 400

        db.session.execute(
            subject_relations.delete().where(
                ((subject_relations.c.subject_id == subject.id) &
                 (subject_relations.c.related_subject_id == related_id)) |
                ((subject_relations.c.subject_id == related_id) &
                 (subject_relations.c.related_subject_id == subject.id))
            )
        )

        AuditLog.log(
            user_id=current_user.id,
            action='delete',
            entity_type='subject_relation',
            entity_id=f"{subject.id}-{related_id}",
            ip_address=request.remote_addr,
            description=f"Removed relationship between {subject.name} and subject {related_id}"
        )
        db.session.commit()

        return jsonify({'message': 'Relationship removed'})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error removing relationship: {e} ({type(e).__name__})")
        return jsonify({'error': str(e)}), 500


@cms_bp.route('/subjects/<subject_id>/delete', methods=['POST'])
@login_required
@roles_required('admin', 'senior_investigator')
def delete_subject(subject_id: str):
    """Soft-delete a subject if not linked to any case."""
    subject = db.session.get(Subject, subject_id) or abort(404)

    # Check if subject is linked to any active case
    linked_cases = [c for c in Case.query.filter_by(
        is_deleted=False).all() if subject in c.subjects.all()]
    if linked_cases:
        case_list = ', '.join(
            [f'{c.case_number} ({c.title})' for c in linked_cases[:5]])
        extra = f' and {len(linked_cases)-5} more' if len(linked_cases) > 5 else ''
        return jsonify({
            'error': f'Kan subject niet verwijderen: gekoppeld aan {len(linked_cases)} za(a)k(en): {case_list}{extra}'
        }), 400

    subject.soft_delete()

    AuditLog.log(
        user_id=current_user.id,
        action='delete',
        entity_type='subject',
        entity_id=subject_id,
        ip_address=request.remote_addr,
        description=f"Deleted subject: {subject.name}"
    )
    db.session.commit()

    if request.is_json:
        return jsonify({'message': 'Subject verwijderd'})
    flash(f'Subject {subject.name} is verwijderd.', 'info')
    return redirect(url_for('cms.subjects'))
