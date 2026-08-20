SET app.bypass_rls = 'true';
SET app.tenant_id = '3a169c92-52ce-4119-8d40-04bb5078873d';

INSERT INTO subjects (id, tenant_id, subject_type, name, achternaam, voornamen, voorletters, tussenvoegsels, geslacht, date_of_birth, place_of_birth, nationality, bsn_number, identification_number, reisdocument_type, reisdocument_nummer, street, house_number, house_number_addition, postal_code, city, phone, email, bank_account, risk_score, notes, created_by, risk_factors, workflow_social_accounts) VALUES (
  'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
  '3a169c92-52ce-4119-8d40-04bb5078873d',
  'person',
  'Test, Jan Peter van der',
  'Test',
  'Jan Peter',
  'J.P.',
  'van der',
  'man',
  '1990-05-15',
  'Amsterdam',
  'Dutch',
  '123456789',
  'AB1234567',
  'paspoort',
  'PA9988776',
  'Keizersgracht',
  '123',
  'A',
  '1015 CJ',
  'Amsterdam',
  '+31612345678',
  'jan.test@example.com',
  'NL91ABNA0417164300',
  75,
  'Dit is een testsubject voor verificatie van alle velden.',
  (SELECT id FROM users LIMIT 1),
  '["High Value Target", "International Travel"]'::jsonb,
  '["@jan_test", "@peter_test"]'::jsonb
);

-- Add contacts
INSERT INTO contacts (id, tenant_id, subject_id, contact_type, value, is_primary, source) VALUES
  ('aaaaaaaa-0001-0001-0001-aaaaaaaaaaaa', '3a169c92-52ce-4119-8d40-04bb5078873d', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 'email', 'jan.test@example.com', true, 'test'),
  ('aaaaaaaa-0002-0002-0002-aaaaaaaaaaaa', '3a169c92-52ce-4119-8d40-04bb5078873d', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 'phone', '+31612345678', false, 'test');

-- Add to a case
INSERT INTO case_subjects (case_id, subject_id, role_in_case, status)
SELECT '82d071da-8af9-487d-8c9d-1f50fa89ca5d', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 'subject', 'active'
WHERE NOT EXISTS (
  SELECT 1 FROM case_subjects WHERE case_id = '82d071da-8af9-487d-8c9d-1f50fa89ca5d' AND subject_id = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
);

-- Verify
SELECT id, LEFT(name,40) as name, subject_type, geslacht, date_of_birth IS NOT NULL as has_dob,
       place_of_birth IS NOT NULL as has_pob, nationality IS NOT NULL as has_nat,
       bsn_number IS NOT NULL as has_bsn, reisdocument_type,
       street IS NOT NULL as has_street, email IS NOT NULL as has_email,
       phone IS NOT NULL as has_phone, bank_account IS NOT NULL as has_bank,
       risk_score, notes IS NOT NULL as has_notes,
       workflow_social_accounts IS NOT NULL as has_social
FROM subjects WHERE id = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa';
