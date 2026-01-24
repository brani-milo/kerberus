#!/usr/bin/env python3
"""
Test SQLCipher encryption functionality.

Run with: make test-sqlcipher

This script validates that SQLCipher is properly installed and configured
by performing a complete encryption round-trip test.
"""
import os
import sys
import tempfile
from pathlib import Path

# Add src to Python path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def test_sqlcipher_available():
    """Check if pysqlcipher3 is installed and functional."""
    print("[1/5] Checking pysqlcipher3 availability...")
    try:
        from pysqlcipher3 import dbapi2 as sqlcipher
        print("  pysqlcipher3 is installed")
        return True
    except ImportError:
        print("  ERROR: pysqlcipher3 is not installed")
        print("  Run: pip install pysqlcipher3")
        print("  Note: On macOS, you may need: brew install sqlcipher")
        return False


def test_encryption_roundtrip():
    """
    Perform a complete encryption round-trip test.

    1. Create encrypted database with password
    2. Store sensitive document
    3. Retrieve and verify content
    4. Close database
    5. Attempt to open with WRONG password (should fail)
    6. Reopen with correct password (should succeed)
    7. Cleanup test database
    """
    try:
        from pysqlcipher3 import dbapi2 as sqlcipher
    except ImportError:
        print("  Skipping encryption test (pysqlcipher3 not available)")
        return False

    # Test parameters
    test_password = "test_secure_password_123!"
    wrong_password = "wrong_password"
    test_content = "This is sensitive Swiss legal content: Art. 337 OR"

    # Create temporary test database
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        test_db_path = tmp.name

    try:
        # Step 1: Create encrypted database
        print("[2/5] Creating encrypted database...")
        conn = sqlcipher.connect(test_db_path)
        cursor = conn.cursor()
        cursor.execute(f"PRAGMA key = '{test_password}'")
        cursor.execute("PRAGMA cipher_compatibility = 4")

        # Create test table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Step 2: Store sensitive content
        print("[3/5] Storing sensitive content...")
        cursor.execute("INSERT INTO documents (content) VALUES (?)", (test_content,))
        conn.commit()

        # Step 3: Retrieve and verify
        cursor.execute("SELECT content FROM documents WHERE id = 1")
        retrieved = cursor.fetchone()[0]
        assert retrieved == test_content, "Content mismatch!"
        print("  Content stored and retrieved successfully")

        conn.close()

        # Step 4: Attempt with wrong password (should fail)
        print("[4/5] Testing wrong password rejection...")
        conn_wrong = sqlcipher.connect(test_db_path)
        cursor_wrong = conn_wrong.cursor()
        cursor_wrong.execute(f"PRAGMA key = '{wrong_password}'")
        try:
            cursor_wrong.execute("SELECT * FROM documents")
            print("  ERROR: Wrong password was accepted!")
            conn_wrong.close()
            return False
        except sqlcipher.DatabaseError:
            print("  Wrong password correctly rejected")
            conn_wrong.close()

        # Step 5: Reopen with correct password
        print("[5/5] Reopening with correct password...")
        conn_correct = sqlcipher.connect(test_db_path)
        cursor_correct = conn_correct.cursor()
        cursor_correct.execute(f"PRAGMA key = '{test_password}'")
        cursor_correct.execute("PRAGMA cipher_compatibility = 4")
        cursor_correct.execute("SELECT content FROM documents WHERE id = 1")
        final_content = cursor_correct.fetchone()[0]
        assert final_content == test_content, "Content mismatch on reopen!"
        print("  Database reopened successfully with correct password")
        conn_correct.close()

        return True

    finally:
        # Cleanup
        if os.path.exists(test_db_path):
            os.unlink(test_db_path)
            print(f"\n  Cleanup: Removed test database")


def main():
    print("=" * 60)
    print("KERBERUS SQLCipher Encryption Test")
    print("=" * 60)
    print()

    # Check availability
    if not test_sqlcipher_available():
        print("\nTest FAILED: pysqlcipher3 not available")
        sys.exit(1)

    print()

    # Run encryption test
    if test_encryption_roundtrip():
        print("\n" + "=" * 60)
        print("All encryption tests PASSED!")
        print("=" * 60)
        print("\nSQLCipher is properly configured for zero-knowledge encryption.")
        sys.exit(0)
    else:
        print("\n" + "=" * 60)
        print("Encryption test FAILED!")
        print("=" * 60)
        sys.exit(1)


if __name__ == "__main__":
    main()
