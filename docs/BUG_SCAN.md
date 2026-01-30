# SYSTEMATIC BUG SCAN RESULTS

**Date:** January 24, 2026  
**Scan Type:** Common Python/Trading Bugs  
**Status:** IN PROGRESS

---

## SCANS COMPLETED ✅

### 1. Clock/Time Issues ✅ FIXED
- [x] datetime.utcnow() calls → Replaced with clock.now() (7 files)
- [x] Logging formatters still use datetime.utcnow() (ACCEPTABLE - deferred)

---

## SCANS IN PROGRESS 🔍

### 2. Exception Handling
Searching for:
- Bare `except:` clauses (should be `except Exception:`)
- Missing exception logging
- Silent failures

### 3. Resource Leaks
Searching for:
- File handles not in context managers
- Database connections not closed
- Thread leaks
- WebSocket connections not cleaned up

### 4. SQL Injection
Searching for:
- String formatting in SQL queries
- Missing parameter sanitization

### 5. Type Safety
Searching for:
- float/Decimal mixing
- None without checks
- Division by zero risks

### 6. Concurrency
Searching for:
- Unprotected shared state
- Missing locks
- Race conditions

### 7. API Rate Limits
Searching for:
- Broker API calls without throttler
- Missing backoff/retry logic

### 8. Data Validation
Searching for:
- Missing price/quantity validation
- Stale data not detected
- Incomplete bars accepted

---

## CRITICAL PATTERNS TO FIND

```bash
# Dangerous patterns:
except:                    # Bare except
float(                     # Should use Decimal
/ 0                        # Division by zero
cursor.execute(f"          # SQL injection
datetime.now()             # Should use clock
open(                      # Should use 'with'
Thread(                    # Check cleanup
```

---

**Starting detailed scans now...**
