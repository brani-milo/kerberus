# ENCRYPTED DOSSIER STORAGE

This directory contains SQLCipher encrypted databases for KERBERUS users.
Each file is encrypted with user/firm-specific keys using AES-256-GCM.

## Security Model

### Personal Dossiers
- **Filename**: `user_{uuid}.db`
- **Encryption**: Zero-knowledge (user password-derived key)
- **Access**: Only the user can decrypt their own dossier
- **Key Management**: Derived from user password via PBKDF2 (256,000 iterations)

### Firm Dossiers
- **Filename**: `firm_{uuid}.db`
- **Encryption**: Master key encrypted
- **Access**: All firm members with appropriate permissions
- **Key Management**: Master key stored in KMS (production) or file (dev only)

## Directory Security

This directory MUST have restricted permissions:
```bash
chmod 700 data/dossier
```

Only the application user should have access to this directory.

## CRITICAL WARNINGS

1. **DO NOT** manually edit or delete these files
   - Corruption will result in permanent data loss
   - There is no recovery mechanism by design

2. **DO NOT** commit these files to version control
   - .gitignore should exclude `*.db` in this directory
   - Even encrypted files should never be in git

3. **Password loss = Permanent data loss**
   - This is intentional (zero-knowledge design)
   - We cannot recover user data without their password
   - There is no "forgot password" for dossier content

4. **Backup Responsibility**
   - Users are responsible for their own backups
   - Firm admins are responsible for firm dossier backups
   - Encrypted backups retain the same encryption

## File Format

Each database follows the SQLCipher 4.x format:
- Encryption: AES-256-GCM
- Key derivation: PBKDF2-SHA512
- Iterations: 256,000 (configurable via SQLCIPHER_ITERATIONS)
- Page size: 4096 bytes

## Troubleshooting

### "Database is not a valid SQLite database"
- Wrong password provided
- File corruption
- Incompatible SQLCipher version

### "Permission denied"
- Check directory permissions: `ls -la data/dossier`
- Should be: `drwx------` (700)

### Database grows unexpectedly
- Run VACUUM after large deletions
- Check MAX_DOSSIER_SIZE_MB setting
