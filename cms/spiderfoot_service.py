"""
SpiderFoot OSINT Integration Service
=====================================
Provides integration with SpiderFoot API for automated OSINT scanning.

SpiderFoot is an open source intelligence (OSINT) automation tool that integrates
with 200+ data sources for reconnaissance and threat intelligence.

Features:
- Start/stop SpiderFoot scans
- Retrieve scan results
- Map SpiderFoot findings to Iveras findings
- Support for multiple target types (domain, email, IP, etc.)
"""

import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class SpiderFootConfig:
    """Configuration for SpiderFoot API connection."""
    base_url: str = "http://localhost:5001"
    username: str = "admin"
    password: str = ""
    timeout: int = 30


@dataclass
class ScanTarget:
    """Represents a scan target."""
    value: str
    target_type: str  # DOMAIN_NAME, EMAILADDR, IP_ADDRESS, etc.
    
    @classmethod
    def from_subject(cls, subject_data: Dict[str, Any]) -> Optional['ScanTarget']:
        """Create a scan target from a subject dictionary."""
        subject_type = subject_data.get('subject_type', '').lower()
        name = subject_data.get('name', '')
        
        # Map Iveras subject types to SpiderFoot target types
        type_mapping = {
            'person': 'NAME',  # Could also try EMAILADDR if available
            'company': 'DOMAIN_NAME',
            'organization': 'DOMAIN_NAME',
            'vehicle': None,  # No direct mapping - use VIN or license plate
            'vessel': None,
            'property': 'INTERNET_NAME',
        }
        
        sf_type = type_mapping.get(subject_type)
        
        # For persons, try to extract email from identifiers
        if subject_type == 'person':
            email = subject_data.get('email')
            if email:
                return cls(value=email, target_type='EMAILADDR')
            # Could try phone number if available
            phone = subject_data.get('phone')
            if phone:
                return cls(value=phone.replace(' ', '').replace('-', ''), target_type='PHONE_NUMBER')
        
        # For companies/organizations, use name as domain guess
        if sf_type == 'DOMAIN_NAME' and '@' not in name and not name.startswith('http'):
            # Assume .com if no domain provided
            if '.' not in name:
                name = f"{name.lower().replace(' ', '')}.com"
            return cls(value=name, target_type='DOMAIN_NAME')
        
        return cls(value=name, target_type=sf_type) if sf_type else None


@dataclass
class ScanResult:
    """Represents a SpiderFoot scan result."""
    scan_id: str
    status: str
    target: str
    target_type: str
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    element_count: int = 0
    errors: List[str] = None
    
    def __post_init__(self):
        if self.errors is None:
            self.errors = []


class SpiderFootService:
    """
    Service class for interacting with SpiderFoot REST API.
    
    This service provides methods to:
    - Check SpiderFoot server status
    - Start and manage scans
    - Retrieve scan results
    - Map findings to Iveras format
    """
    
    TARGET_TYPES = {
        'domain': 'DOMAIN_NAME',
        'email': 'EMAILADDR',
        'email_address': 'EMAILADDR',
        'ip': 'IP_ADDRESS',
        'ip_address': 'IP_ADDRESS',
        'ipv6': 'IPV6_ADDRESS',
        'phone': 'PHONE_NUMBER',
        'phone_number': 'PHONE_NUMBER',
        'bitcoin_address': 'BITCOIN_ADDRESS',
        'bitcoin': 'BITCOIN_ADDRESS',
        'username': 'USERNAME',
        'person': 'NAME',
        'name': 'NAME',
        'company': 'COMPANY_NAME',
        'organization': 'ORGSTITLE',
        'subdomain': 'INTERNET_NAME',
        'url': 'INTERNET_NAME_WEB_CONTENT',
        'vessel': 'VESSEL',
        'license_plate': 'PLATE_NUMBER',
        'vin': 'VEHICLE_VIN',
    }
    
    # Use cases for SpiderFoot scans
    USE_CASES = {
        'passive': 'Passive (no direct contact)',
        'footprint': 'Footprint (limited contact)',
        'investigate': 'Investigate (some active scans)',
        'all': 'All (including aggressive)'
    }
    
    # Common modules by category
    MODULE_CATEGORIES = {
        'dns': ['sfp_dnsresolve', 'sfp_dnsbrute', 'sfp_dnscommonsrv'],
        'whois': ['sfp_whois', 'sfp_whoisology'],
        'threat_intel': ['sfp_abusech', 'sfp_virustotal', 'sfp_alienvault', 'sfp_threatfox'],
        'social': ['sfp_accounts', 'sfp_github', 'sfp_linkedin'],
        'leak_check': ['sfp_haveibeenpwned', 'sfp_hunter'],
        'web': ['sfp_whois', 'sfp_dnsresolve', 'sfp_builtwith'],
        'email': ['sfp_emailformat', 'sfp_hunter', 'sfp_gravatar'],
        'ssl': ['sfp_sslinfo', 'sfp_sslcertspotter'],
        'dns_audit': ['sfp_dnsbrute', 'sfp_dnscommonsrv', 'sfp_dnsresolve'],
        'port_scan': ['sfp_portscan_tcp'],
    }
    
    # Recommended module sets by investigation type
    INVESTIGATION_PROFILES = {
        'basic': {
            'name': 'Basic OSINT',
            'description': 'Essential passive reconnaissance',
            'modules': [
                'sfp_dnsresolve',
                'sfp_dnscommonsrv',
                'sfp_whois',
                'sfp_builtwith',
                'sfp_spyonweb',
                'sfp_dnsbrute',
            ]
        },
        'full': {
            'name': 'Full OSINT',
            'description': 'Comprehensive passive and semi-active scanning',
            'modules': [
                'sfp_dnsresolve',
                'sfp_dnscommonsrv',
                'sfp_dnsbrute',
                'sfp_whois',
                'sfp_whoisology',
                'sfp_builtwith',
                'sfp_spyonweb',
                'sfp_sslcert',
                'sfp_emailformat',
                'sfp_hunter',
                'sfp_accounts',
                'sfp_github',
                'sfp_haveibeenpwned',
                'sfp_gravatar',
            ]
        },
        'threat_hunt': {
            'name': 'Threat Hunting',
            'description': 'Focus on threat intelligence and malware',
            'modules': [
                'sfp_abusech',
                'sfp_virustotal',
                'sfp_alienvault',
                'sfp_threatfox',
                'sfp_haveibeenpwned',
                'sfp_ipstack',
                'sfp_ipinfo',
                'sfp_shodan',
                'sfp_greynoise',
            ]
        },
        'investigation': {
            'name': 'Person/Username Investigation',
            'description': 'Focus on account discovery and breach lookups',
            'modules': [
                'sfp_accounts',      # Searches 500+ sites for usernames
                'sfp_github',        # GitHub repo discovery
                'sfp_hunter',        # Email hunter
                'sfp_haveibeenpwned', # Breach database
                'sfp_gravatar',      # Gravatar lookup
                'sfp_emailformat',   # Email format validation
            ]
        },
        'company': {
            'name': 'Company Reconnaissance',
            'description': 'Focus on corporate information gathering',
            'modules': [
                'sfp_builtwith',
                'sfp_spyonweb',
                'sfp_dnsbrute',
                'sfp_whois',
                'sfp_whoisology',
                'sfp_virustotal',
                'sfp_dnsresolve',
                'sfp_sslcert',
                'sfp_github',
            ]
        },
    }
    
    def __init__(self, config: Optional[SpiderFootConfig] = None):
        """
        Initialize the SpiderFoot service.
        
        Args:
            config: SpiderFoot API configuration. If None, uses defaults.
        """
        self.config = config or SpiderFootConfig()
        self._client = None
        self._connected = False
    
    @property
    def client(self):
        """Lazy-load the SpiderFoot client."""
        if self._client is None:
            try:
                from spiderfoot_client import SpiderFootClient
                self._client = SpiderFootClient(
                    base_url=self.config.base_url,
                    username=self.config.username,
                    password=self.config.password
                )
            except ImportError:
                logger.error("spiderfoot-client not installed. Run: pip install spiderfoot-client")
                return None
        return self._client
    
    def is_available(self) -> bool:
        """
        Check if SpiderFoot server is available and responsive.
        
        Returns:
            True if SpiderFoot is reachable, False otherwise.
        """
        if not self.client:
            return False
        
        try:
            result = self.client.ping()
            self._connected = result is not None
            return self._connected
        except Exception as e:
            logger.error(f"SpiderFoot connection failed: {e}")
            self._connected = False
            return False
    
    def get_server_info(self) -> Optional[Dict[str, Any]]:
        """
        Get SpiderFoot server information.
        
        Returns:
            Server info dict or None if unavailable.
        """
        if not self.client:
            return None
        
        try:
            # Try to get scan list as a way to verify connection
            scans = self.client.get_scan_list()
            return {'status': 'connected', 'version': '4.0+', 'scan_count': len(scans) if scans else 0}
        except Exception as e:
            logger.error(f"Failed to get SpiderFoot info: {e}")
            return {'status': 'connected', 'note': 'API accessible'}
    
    def get_available_modules(self) -> List[Dict[str, str]]:
        """
        Get list of available SpiderFoot modules.
        
        Returns:
            List of module info dicts with 'name' and 'description'.
        """
        if not self.client:
            return []
        
        try:
            modules = self.client.get_modules()
            return modules if modules else []
        except Exception as e:
            logger.error(f"Failed to list modules: {e}")
            return []
    
    def start_scan(
        self,
        target: str,
        target_type: str = 'DOMAIN_NAME',
        scan_name: Optional[str] = None,
        use_case: str = 'passive',
        module_ids: Optional[List[str]] = None,
        profile: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Start a SpiderFoot scan.
        
        Args:
            target: The target value (domain, email, IP, etc.)
            target_type: SpiderFoot target type (e.g., 'DOMAIN_NAME')
            scan_name: Optional custom scan name
            use_case: Scan use case ('passive', 'footprint', 'investigate', 'all')
            module_ids: Optional list of specific module IDs to run
            profile: Use a predefined investigation profile
            
        Returns:
            Scan info dict with 'scan_id' and status, or None on failure.
        """
        if not self.client:
            logger.error("SpiderFoot client not available")
            return None
        
        if not scan_name:
            scan_name = f"Iveras Scan - {target} - {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        
        # Determine which modules to use
        if profile and profile in self.INVESTIGATION_PROFILES:
            module_ids = self.INVESTIGATION_PROFILES[profile]['modules']
        elif module_ids is None:
            # Default to basic modules
            module_ids = self.INVESTIGATION_PROFILES['basic']['modules']
        
        try:
            result = self.client.start_scan(
                target=target,
                scan_name=scan_name,
                use_case=use_case,
                modules=module_ids
            )
            
            scan_id = result.get('scan_id') or result.get('id')
            logger.info(f"Started SpiderFoot scan {scan_id} for {target}")
            return {'scan_id': scan_id, 'status': 'success', **result}
            
        except Exception as e:
            logger.error(f"Failed to start scan: {e}")
            return None
    
    def get_scan_status(self, scan_id: str) -> Optional[Dict[str, Any]]:
        """
        Get the status of a SpiderFoot scan.
        
        Args:
            scan_id: The SpiderFoot scan ID
            
        Returns:
            Status dict with 'status', 'progress', etc.
        """
        if not self.client:
            return None
        
        try:
            status = self.client.get_scan_status(scan_id)
            return status
        except Exception as e:
            logger.error(f"Failed to get scan status: {e}")
            return None
    
    def get_scan_results(
        self,
        scan_id: str,
        element_type: Optional[str] = None,
        limit: int = 10000
    ) -> List[Dict[str, Any]]:
        """
        Get results from a SpiderFoot scan.
        
        Args:
            scan_id: The SpiderFoot scan ID
            element_type: Optional filter by element type (e.g., 'EMAILADDR')
            limit: Maximum number of results to return (client-side filtering)
            
        Returns:
            List of result dicts.
        """
        if not self.client:
            return []
        
        try:
            results = self.client.get_scan_results(scan_id, event_type=element_type)
            
            # Normalize results to dict format
            normalized = self.normalize_results(results)
            
            # Apply limit client-side
            if limit and len(normalized) > limit:
                normalized = normalized[:limit]
            
            return normalized
        except Exception as e:
            logger.error(f"Failed to get scan results: {e}")
            return []
    
    def get_scan_list(self) -> List[Dict[str, Any]]:
        """
        Get list of all SpiderFoot scans.
        
        Returns:
            List of scan info dicts.
        """
        if not self.client:
            return []
        
        try:
            scans = self.client.get_scan_list()
            return scans if scans else []
        except Exception as e:
            logger.error(f"Failed to list scans: {e}")
            return []
    
    def stop_scan(self, scan_id: str) -> bool:
        """
        Stop a running SpiderFoot scan.
        
        Args:
            scan_id: The SpiderFoot scan ID
            
        Returns:
            True if successful, False otherwise.
        """
        if not self.client:
            return False
        
        try:
            self.client.stop_scan(scan_id)
            logger.info(f"Stopped SpiderFoot scan {scan_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to stop scan: {e}")
            return False
    
    def delete_scan(self, scan_id: str) -> bool:
        """
        Delete a SpiderFoot scan and its data.
        
        Args:
            scan_id: The SpiderFoot scan ID
            
        Returns:
            True if successful, False otherwise.
        """
        if not self.client:
            return False
        
        try:
            self.client.delete_scan(scan_id)
            logger.info(f"Deleted SpiderFoot scan {scan_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete scan: {e}")
            return False
    
    def export_scan(self, scan_id: str, format: str = 'json') -> Optional[str]:
        """
        Export scan data in specified format.
        
        Args:
            scan_id: The SpiderFoot scan ID
            format: Export format ('json', 'csv', 'html')
            
        Returns:
            Exported data as string, or None on failure.
        """
        if not self.client:
            return None
        
        try:
            export = self.client.export_scan_results(scan_id, format=format)
            return export
        except Exception as e:
            logger.error(f"Failed to export scan: {e}")
            return None
    
    def map_to_iveras_finding(self, sf_result: Dict[str, Any], case_id: str, subject_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Map a SpiderFoot result to Iveras finding format.
        
        Args:
            sf_result: SpiderFoot result dict
            case_id: Iveras case ID to link the finding to
            subject_id: Optional Iveras subject ID
            
        Returns:
            Dict suitable for creating an Iveras Finding.
        """
        sf_type = sf_result.get('type', 'UNKNOWN')
        
        # Map SpiderFoot types to Iveras finding types
        type_mapping = {
            'EMAILADDR': 'identity',
            'PHONE_NUMBER': 'identity',
            'NAME': 'identity',
            'INTERNET_NAME': 'location',
            'IP_ADDRESS': 'network',
            'DOMAIN_NAME': 'network',
            'SOCIAL_MEDIA': 'connection',
            'ACCOUNT': 'connection',
            'USERNAME': 'identity',
            'BITCOIN_ADDRESS': 'financial',
            'WALLET_ADDRESS': 'financial',
            'LEAKS': 'breach',
            'BREACH': 'breach',
            'VULNERABILITY': 'threat',
            'MALWARE': 'threat',
            'PORTSCAN': 'network',
            'TCP_PORT_OPEN': 'network',
            'DNS_RECORD': 'network',
            'WHOIS': 'identity',
            'SSL_CERTIFICATE': 'network',
            'GEOIP': 'location',
            'CO_HOSTED_SITE': 'network',
            'AFFILIATE': 'connection',
            'HUMAN_NAME': 'identity',
            'COMPANY': 'identity',
            'COMPANY_NAME': 'identity',
            'AFFILIATE_DOMAINNAME': 'connection',
        }
        
        finding_type = type_mapping.get(sf_type, 'general')
        
        # Determine confidence level
        data_turned = sf_result.get('dataTransformed', sf_result.get('data', ''))
        if len(data_turned) > 100:
            confidence = 'high'
        elif len(data_turned) > 20:
            confidence = 'medium'
        else:
            confidence = 'low'
        
        return {
            'case_id': case_id,
            'subject_id': subject_id,
            'title': f"[SpiderFoot] {sf_type}: {data_turned[:100]}",
            'content': f"**Source:** SpiderFoot\n**Type:** {sf_type}\n**Data:** {data_turned}\n\n**Module:** {sf_result.get('sourceModule', 'Unknown')}",
            'source_url': sf_result.get('sourceUrl', ''),
            'source_type': 'spiderfoot',
            'reliability_score': 7,  # SpiderFoot data is generally reliable
            'confidence_level': confidence,
            'finding_type': finding_type,
            'tags': ['spiderfoot', sf_type.lower()],
        }
    
    def get_result_summary(self, results: List) -> Dict[str, int]:
        """
        Generate a summary of scan results by type.
        
        Args:
            results: List of SpiderFoot results (dict or list format)
            
        Returns:
            Dict mapping type names to counts.
        """
        summary = {}
        for result in results:
            # Handle both list and dict formats
            if isinstance(result, list):
                # List format: [..., type, ...] - type is typically last
                sf_type = result[-1] if result else 'UNKNOWN'
            else:
                sf_type = result.get('type', 'UNKNOWN')
            summary[sf_type] = summary.get(sf_type, 0) + 1
        
        return dict(sorted(summary.items(), key=lambda x: x[1], reverse=True))
    
    def normalize_result(self, result: list) -> Dict[str, Any]:
        """
        Normalize a SpiderFoot result to a dictionary format.
        
        List format: [timestamp, data, value, source_module, ..., type]
        """
        if isinstance(result, dict):
            return result
        
        if isinstance(result, list) and len(result) >= 3:
            return {
                'timestamp': result[0] if len(result) > 0 else None,
                'data': result[1] if len(result) > 1 else None,
                'value': result[2] if len(result) > 2 else None,
                'sourceModule': result[3] if len(result) > 3 else None,
                'type': result[-1] if result else 'UNKNOWN',  # type is usually last
                'raw': result
            }
        
        return {'raw': result}
    
    def normalize_results(self, results: List) -> List[Dict[str, Any]]:
        """
        Normalize all results to dictionary format.
        """
        return [self.normalize_result(r) for r in results]


# Global service instance
_spiderfoot_service: Optional[SpiderFootService] = None


def get_spiderfoot_service(config: Optional[SpiderFootConfig] = None) -> SpiderFootService:
    """
    Get or create the global SpiderFoot service instance.
    
    Args:
        config: Optional SpiderFoot configuration
        
    Returns:
        SpiderFootService instance
    """
    global _spiderfoot_service
    
    if _spiderfoot_service is None or config is not None:
        _spiderfoot_service = SpiderFootService(config)
    
    return _spiderfoot_service
