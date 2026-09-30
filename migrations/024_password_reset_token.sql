-- =============================================================================
-- RadioMonitor -- Migration 024: Password reset tokens
-- =============================================================================
-- Self-service "Forgot password" flow, mirroring UserInvitation's pattern:
-- a time-limited, single-use token emailed to the user, redeemed to set a
-- new password. No PII beyond what the User row already has.
-- =============================================================================

USE RadioMonitor;
GO
SET QUOTED_IDENTIFIER ON;
SET XACT_ABORT ON;
GO
BEGIN TRANSACTION;

IF OBJECT_ID('dbo.PasswordResetToken','U') IS NULL
BEGIN
    CREATE TABLE dbo.PasswordResetToken (
        TokenID     INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        UserID      INT               NOT NULL,
        Token       NVARCHAR(100)     NOT NULL UNIQUE,
        ExpiresAt   DATETIME2         NOT NULL,
        UsedAt      DATETIME2         NULL,
        CreatedAt   DATETIME2         NOT NULL
            CONSTRAINT DF_PasswordResetToken_CreatedAt DEFAULT SYSDATETIME(),

        CONSTRAINT FK_PasswordResetToken_User
            FOREIGN KEY (UserID) REFERENCES dbo.[User](UserID)
    );

    CREATE INDEX IX_PasswordResetToken_Token
        ON dbo.PasswordResetToken(Token);
END

COMMIT TRANSACTION;
GO

PRINT '--- PasswordResetToken ---';
SELECT name FROM sys.tables WHERE name = 'PasswordResetToken';

PRINT '024 applied.';
