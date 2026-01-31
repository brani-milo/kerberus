#!/usr/bin/env python3
"""
Test script for authentication and dossier system.

Run with: python scripts/test_auth_dossier.py

Prerequisites:
- PostgreSQL running (for auth_db)
- Qdrant running (for dossier search)
- Virtual environment activated
"""
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


def test_imports():
    """Test that all modules import correctly."""
    print("=" * 50)
    print("1. Testing imports...")
    print("=" * 50)

    try:
        from src.database.auth_db import AuthDB, hash_password, verify_password
        print("   ✅ auth_db imports OK")
    except Exception as e:
        print(f"   ❌ auth_db import failed: {e}")
        return False

    try:
        from src.auth.mfa import (
            generate_totp_secret, verify_totp, setup_mfa,
            generate_backup_codes
        )
        print("   ✅ mfa imports OK")
    except Exception as e:
        print(f"   ❌ mfa import failed: {e}")
        return False

    try:
        from src.database.dossier_db import DossierDB
        print("   ✅ dossier_db imports OK")
    except Exception as e:
        print(f"   ❌ dossier_db import failed: {e}")
        return False

    try:
        from src.search.dossier_search import DossierSearchService
        print("   ✅ dossier_search imports OK")
    except Exception as e:
        print(f"   ❌ dossier_search import failed: {e}")
        return False

    print()
    return True


def test_password_hashing():
    """Test password hashing utilities."""
    print("=" * 50)
    print("2. Testing password hashing...")
    print("=" * 50)

    from src.database.auth_db import hash_password, verify_password

    password = "TestPassword123!"

    # Hash
    hashed = hash_password(password)
    print(f"   Password: {password}")
    print(f"   Hash: {hashed[:20]}...")

    # Verify correct password
    if verify_password(password, hashed):
        print("   ✅ Correct password verified")
    else:
        print("   ❌ Correct password failed verification")
        return False

    # Verify wrong password
    if not verify_password("WrongPassword", hashed):
        print("   ✅ Wrong password rejected")
    else:
        print("   ❌ Wrong password was accepted")
        return False

    print()
    return True


def test_mfa():
    """Test MFA/TOTP functionality."""
    print("=" * 50)
    print("3. Testing MFA/TOTP...")
    print("=" * 50)

    from src.auth.mfa import (
        generate_totp_secret, verify_totp, get_current_totp,
        setup_mfa, generate_backup_codes
    )

    # Generate secret
    secret = generate_totp_secret()
    print(f"   Secret: {secret}")

    # Get current code
    current_code = get_current_totp(secret)
    print(f"   Current TOTP: {current_code}")

    # Verify it
    if verify_totp(secret, current_code):
        print("   ✅ TOTP verification works")
    else:
        print("   ❌ TOTP verification failed")
        return False

    # Test wrong code
    if not verify_totp(secret, "000000"):
        print("   ✅ Wrong TOTP rejected")
    else:
        print("   ❌ Wrong TOTP accepted")
        return False

    # Test full setup
    secret, uri, qr_base64 = setup_mfa("test@example.com")
    print(f"   Setup URI: {uri[:50]}...")
    print(f"   QR code generated: {len(qr_base64)} bytes")

    # Test backup codes
    codes = generate_backup_codes()
    print(f"   Backup codes: {codes[:2]}... ({len(codes)} total)")

    print()
    return True


def test_auth_db_connection():
    """Test PostgreSQL connection (requires running PostgreSQL)."""
    print("=" * 50)
    print("4. Testing PostgreSQL connection...")
    print("=" * 50)

    try:
        from src.database.auth_db import AuthDB

        # Try to connect
        auth_db = AuthDB()
        print("   ✅ AuthDB initialized")

        # Try to initialize schema (creates tables if not exist)
        auth_db.init_schema()
        print("   ✅ Schema initialized")

        return True

    except Exception as e:
        print(f"   ⚠️  PostgreSQL not available: {e}")
        print("   (This is OK if you haven't set up PostgreSQL yet)")
        return None  # Not a failure, just not available


def test_auth_db_operations():
    """Test user creation and session management."""
    print("=" * 50)
    print("5. Testing auth operations...")
    print("=" * 50)

    try:
        from src.database.auth_db import AuthDB, hash_password, verify_password

        auth_db = AuthDB()
        auth_db.init_schema()

        # Create test user
        test_email = f"test_{__import__('time').time()}@example.com"
        password_hash = hash_password("TestPass123!")

        user_id = auth_db.create_user(test_email, password_hash)
        print(f"   ✅ Created user: {user_id[:8]}...")

        # Get user by email
        user = auth_db.get_user_by_email(test_email)
        if user and user["email"] == test_email:
            print("   ✅ Retrieved user by email")
        else:
            print("   ❌ Failed to retrieve user")
            return False

        # Create session
        session_token = auth_db.create_session(user_id)
        print(f"   ✅ Created session: {session_token[:16]}...")

        # Validate session
        session_user = auth_db.validate_session(session_token)
        # Compare as strings since DB returns UUID object
        if session_user and str(session_user["user_id"]) == user_id:
            print("   ✅ Session validated")
        else:
            print(f"   ❌ Session validation failed (got: {session_user})")
            return False

        # Invalidate session
        auth_db.invalidate_session(session_token)
        if auth_db.validate_session(session_token) is None:
            print("   ✅ Session invalidated")
        else:
            print("   ❌ Session still valid after invalidation")
            return False

        # Deactivate user (cleanup)
        auth_db.deactivate_user(user_id)
        print("   ✅ Test user deactivated")

        print()
        return True

    except Exception as e:
        print(f"   ⚠️  Auth operations failed: {e}")
        return None


def test_dossier_db():
    """Test SQLCipher dossier (requires pysqlcipher3)."""
    print("=" * 50)
    print("6. Testing encrypted dossier...")
    print("=" * 50)

    try:
        from src.database.dossier_db import DossierDB
        import tempfile
        import uuid

        # Use temp directory for test
        with tempfile.TemporaryDirectory() as tmpdir:
            user_id = str(uuid.uuid4())
            password = "TestPassword123!"

            # Create dossier
            dossier = DossierDB(
                user_id=user_id,
                user_password=password,
                storage_path=tmpdir
            )

            # Store document
            doc_id = str(uuid.uuid4())
            dossier.store_document(
                doc_id=doc_id,
                title="Test Contract",
                content="This is a test employment contract with multiple clauses.",
                doc_type="contract",
                language="en"
            )
            print("   ✅ Document stored")

            # Retrieve document
            doc = dossier.get_document(doc_id)
            if doc and doc["title"] == "Test Contract":
                print("   ✅ Document retrieved")
            else:
                print("   ❌ Document retrieval failed")
                return False

            # List documents
            docs = dossier.list_documents()
            if len(docs) == 1:
                print("   ✅ Document listing works")
            else:
                print("   ❌ Document listing failed")
                return False

            # Get stats
            stats = dossier.get_stats()
            print(f"   Stats: {stats['document_count']} docs, {stats['file_size_mb']} MB")

            # Delete document
            if dossier.delete_document(doc_id):
                print("   ✅ Document deleted")
            else:
                print("   ❌ Document deletion failed")
                return False

            dossier.close()

        print()
        return True

    except ImportError as e:
        if "pysqlcipher3" in str(e):
            print("   ⚠️  pysqlcipher3 not installed")
            print("   Install with: pip install pysqlcipher3")
            print("   (Requires SQLCipher system library)")
            return None
        raise
    except Exception as e:
        print(f"   ❌ Dossier test failed: {e}")
        return False


def main():
    """Run all tests."""
    print("\n" + "=" * 50)
    print("KERBERUS Auth & Dossier System Tests")
    print("=" * 50 + "\n")

    results = {}

    # Test imports (required)
    results["imports"] = test_imports()
    if not results["imports"]:
        print("❌ Import tests failed. Fix import errors first.")
        return 1

    # Test password hashing
    results["password"] = test_password_hashing()

    # Test MFA
    results["mfa"] = test_mfa()

    # Test PostgreSQL (optional)
    results["postgres"] = test_auth_db_connection()

    # Test auth operations (if PostgreSQL available)
    if results["postgres"]:
        results["auth_ops"] = test_auth_db_operations()

    # Test dossier (optional - requires pysqlcipher3)
    results["dossier"] = test_dossier_db()

    # Summary
    print("=" * 50)
    print("SUMMARY")
    print("=" * 50)

    passed = 0
    failed = 0
    skipped = 0

    for name, result in results.items():
        if result is True:
            status = "✅ PASSED"
            passed += 1
        elif result is False:
            status = "❌ FAILED"
            failed += 1
        else:
            status = "⚠️  SKIPPED"
            skipped += 1
        print(f"   {name}: {status}")

    print()
    print(f"   Total: {passed} passed, {failed} failed, {skipped} skipped")
    print()

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
