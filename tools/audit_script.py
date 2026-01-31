#!/usr/bin/env python3
"""
COMPREHENSIVE CODE AUDIT SCRIPT
Tests for common bugs across entire MiniQuantDeskv2 codebase
"""

import sys
import ast
import re
from pathlib import Path
from typing import List, Dict, Tuple

# Bug categories
BUGS_FOUND = {
    'datetime_utcnow': [],
    'float_decimal_mixing': [],
    'missing_none_checks': [],
    'division_by_zero': [],
    'bare_except': [],
    'sql_injection_risk': [],
    'resource_leaks': [],
    'mutable_defaults': [],
    'import_errors': [],
    'type_errors': [],
}

def scan_file(filepath: Path) -> Dict[str, List[str]]:
    """Scan a single Python file for bugs."""
    issues = {k: [] for k in BUGS_FOUND.keys()}
    
    try:
        content = filepath.read_text(encoding='utf-8')
        lines = content.split('\n')
        
        # Pattern-based checks
        for i, line in enumerate(lines, 1):
            line_num = f"{filepath}:{i}"
            
            # 1. datetime.utcnow() usage
            if 'datetime.utcnow' in line and 'DATETIME_FIX' not in line:
                issues['datetime_utcnow'].append(f"{line_num} - {line.strip()}")
            
            # 2. Float/Decimal mixing
            if re.search(r'Decimal\(["\']?\d+\.\d+["\']?\)', line):
                # Good: Decimal("1.5")
                pass
            elif re.search(r'Decimal\(\d+\.\d+\)', line):
                # Bad: Decimal(1.5) - float input
                issues['float_decimal_mixing'].append(f"{line_num} - {line.strip()}")
            
            # 3. Division without zero check
            if '/' in line and 'if' not in line and '#' not in line[:line.find('/') if '/' in line else 0]:
                # Potential division by zero
                if any(var in line for var in ['count', 'total', 'size', 'length', 'num']):
                    issues['division_by_zero'].append(f"{line_num} - {line.strip()}")
            
            # 4. Bare except
            if re.match(r'\s*except\s*:', line):
                issues['bare_except'].append(f"{line_num} - {line.strip()}")
            
            # 5. SQL injection risk (string formatting in SQL)
            if ('execute(' in line or 'executemany(' in line) and ('f"' in line or '.format(' in line or '%' in line):
                issues['sql_injection_risk'].append(f"{line_num} - {line.strip()}")
            
            # 6. Resource leaks (open without with)
            if 'open(' in line and 'with ' not in line and '#' not in line[:line.find('open')]:
                issues['resource_leaks'].append(f"{line_num} - {line.strip()}")
        
        # AST-based checks
        try:
            tree = ast.parse(content)
            
            # Mutable default arguments
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef):
                    for default in node.args.defaults:
                        if isinstance(default, (ast.List, ast.Dict, ast.Set)):
                            issues['mutable_defaults'].append(
                                f"{filepath}:{node.lineno} - Function '{node.name}' has mutable default"
                            )
        except SyntaxError as e:
            issues['import_errors'].append(f"{filepath} - Syntax error: {e}")
    
    except Exception as e:
        issues['import_errors'].append(f"{filepath} - Failed to read: {e}")
    
    return issues

def main():
    """Run comprehensive audit."""
    print("=" * 80)
    print("MINIQUANTDESK v2 - COMPREHENSIVE CODE AUDIT")
    print("=" * 80)
    print()
    
    # Scan all Python files
    repo_path = Path(__file__).parent
    python_files = list(repo_path.rglob("*.py"))
    
    # Exclude test files and venv
    python_files = [
        f for f in python_files 
        if 'venv' not in str(f) 
        and '__pycache__' not in str(f)
        and 'audit_script.py' not in str(f)
    ]
    
    print(f"Scanning {len(python_files)} Python files...")
    print()
    
    # Scan each file
    for filepath in python_files:
        file_issues = scan_file(filepath)
        for category, issues in file_issues.items():
            BUGS_FOUND[category].extend(issues)
    
    # Report results
    print("=" * 80)
    print("AUDIT RESULTS")
    print("=" * 80)
    print()
    
    total_issues = 0
    
    for category, issues in BUGS_FOUND.items():
        count = len(issues)
        total_issues += count
        
        if count > 0:
            print(f"\n{'='*80}")
            print(f"{category.upper().replace('_', ' ')}: {count} issues")
            print('='*80)
            
            # Show first 10
            for issue in issues[:10]:
                print(f"  {issue}")
            
            if count > 10:
                print(f"  ... and {count - 10} more")
    
    print()
    print("=" * 80)
    print(f"TOTAL ISSUES FOUND: {total_issues}")
    print("=" * 80)
    
    # Critical issues
    critical = (
        BUGS_FOUND['datetime_utcnow'] +
        BUGS_FOUND['sql_injection_risk'] +
        BUGS_FOUND['import_errors']
    )
    
    if critical:
        print()
        print("⚠️  CRITICAL ISSUES MUST BE FIXED BEFORE PRODUCTION")
        print(f"   - {len(BUGS_FOUND['datetime_utcnow'])} datetime.utcnow() calls")
        print(f"   - {len(BUGS_FOUND['sql_injection_risk'])} SQL injection risks")
        print(f"   - {len(BUGS_FOUND['import_errors'])} import/syntax errors")
    
    return 0 if total_issues == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
