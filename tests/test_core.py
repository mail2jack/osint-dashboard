import pytest
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cms.app_helpers import validate_email, validate_ip, validate_domain, normalize_phone_number

class TestEmailValidation:
    def test_valid_email(self):
        assert validate_email("test@example.com") == True
        assert validate_email("user.name@domain.co.uk") == True
        assert validate_email("user+tag@gmail.com") == True
    
    def test_invalid_email(self):
        assert validate_email("invalid") == False
        assert validate_email("@example.com") == False
        assert validate_email("test@") == False
        assert validate_email("test @example.com") == False
        assert validate_email("") == False

class TestIPValidation:
    def test_valid_ip(self):
        assert validate_ip("192.168.1.1") == True
        assert validate_ip("10.0.0.1") == True
        assert validate_ip("8.8.8.8") == True
    
    def test_invalid_ip(self):
        assert validate_ip("256.1.1.1") == False
        assert validate_ip("invalid") == False

class TestDomainValidation:
    def test_valid_domain(self):
        assert validate_domain("example.com") == True
        assert validate_domain("my-site.co") == True
    
    def test_invalid_domain(self):
        assert validate_domain("") == False
        assert validate_domain("-invalid.com") == False

class TestPhoneNormalization:
    def test_normalize_with_plus(self):
        assert normalize_phone_number("+31612345678") == "31612345678"
    
    def test_normalize_with_zeros(self):
        assert normalize_phone_number("0031612345678") == "31612345678"
    
    def test_normalize_with_leading_zero(self):
        assert normalize_phone_number("0612345678") == "612345678"
    
    def test_normalize_with_dashes(self):
        assert normalize_phone_number("+31-6-12345678") == "31612345678"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
