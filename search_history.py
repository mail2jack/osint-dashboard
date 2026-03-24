"""
Search history and audit log management
"""

import os
import json
import threading
from datetime import datetime
from collections import defaultdict

class SearchHistory:
    def __init__(self, history_file='search_history.json', archive_file='search_archive.json'):
        self.history_file = history_file
        self.archive_file = archive_file
        self._lock = threading.Lock()
        self._ensure_files()
    
    def _ensure_files(self):
        for f in [self.history_file, self.archive_file]:
            if not os.path.exists(f):
                with open(f, 'w') as file:
                    json.dump([], file)
    
    def _read_json(self, filepath):
        with self._lock:
            try:
                with open(filepath, 'r') as f:
                    return json.load(f)
            except (json.JSONDecodeError, FileNotFoundError):
                return []
    
    def _write_json(self, filepath, data):
        with self._lock:
            with open(filepath, 'w') as f:
                json.dump(data, f, indent=2, default=str)
    
    def add_entry(self, tool, query, results_summary, results_count=0):
        entry = {
            'id': f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{hash(query) % 10000}",
            'timestamp': datetime.now().isoformat(),
            'tool': tool,
            'query': query,
            'results_count': results_count,
            'results_summary': results_summary,
            'status': 'unread',
            'archived': False
        }
        
        history = self._read_json(self.history_file)
        history.insert(0, entry)
        self._write_json(self.history_file, history)
        return entry
    
    def get_history(self, limit=100, status_filter=None):
        history = self._read_json(self.history_file)
        if status_filter:
            history = [h for h in history if h.get('status') == status_filter]
        return history[:limit]
    
    def get_archive(self, limit=500, search_query=None, search_tool=None):
        archive = self._read_json(self.archive_file)
        
        if search_query:
            query_lower = search_query.lower()
            archive = [a for a in archive if 
                       query_lower in a.get('query', '').lower() or
                       query_lower in a.get('results_summary', '').lower()]
        
        if search_tool:
            archive = [a for a in archive if a.get('tool') == search_tool]
        
        return archive[:limit]
    
    def archive_entry(self, entry_id):
        history = self._read_json(self.history_file)
        archive = self._read_json(self.archive_file)
        
        for i, entry in enumerate(history):
            if entry.get('id') == entry_id:
                entry['archived'] = True
                entry['archived_at'] = datetime.now().isoformat()
                entry['status'] = 'archived'
                archive.insert(0, entry)
                history.pop(i)
                break
        
        self._write_json(self.history_file, history)
        self._write_json(self.archive_file, archive)
    
    def mark_read(self, entry_id):
        history = self._read_json(self.history_file)
        for entry in history:
            if entry.get('id') == entry_id:
                entry['status'] = 'read'
                break
        self._write_json(self.history_file, history)
    
    def mark_all_read(self):
        history = self._read_json(self.history_file)
        for entry in history:
            entry['status'] = 'read'
        self._write_json(self.history_file, history)
    
    def archive_all(self):
        history = self._read_json(self.history_file)
        archive = self._read_json(self.archive_file)
        
        for entry in history:
            if not entry.get('archived'):
                entry['archived'] = True
                entry['archived_at'] = datetime.now().isoformat()
                entry['status'] = 'archived'
                archive.insert(0, entry)
        
        self._write_json(self.history_file, [])
        self._write_json(self.archive_file, archive)
        return len(archive)
    
    def get_stats(self):
        history = self._read_json(self.history_file)
        archive = self._read_json(self.archive_file)
        
        unread = len([h for h in history if h.get('status') == 'unread'])
        
        tool_counts = defaultdict(int)
        for h in history + archive:
            tool_counts[h.get('tool', 'unknown')] += 1
        
        return {
            'total_history': len(history),
            'total_archive': len(archive),
            'unread_count': unread,
            'by_tool': dict(tool_counts)
        }

search_history = SearchHistory()
