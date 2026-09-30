-- Migration 027: API keys and signed clip tokens
USE RadioMonitor;
GO
SET QUOTED_IDENTIFIER ON; SET XACT_ABORT ON;
GO
BEGIN TRANSACTION;

IF OBJECT_ID('dbo.ApiKey','U') IS NULL
BEGIN
    CREATE TABLE dbo.ApiKey (
        ApiKeyID        INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        SubscriberID    INT               NOT NULL,
        KeyHash         NVARCHAR(128)     NOT NULL,
        KeyPrefix       NVARCHAR(16)      NOT NULL,  -- first 8 chars for display
        Label           NVARCHAR(100)     NULL,
        IsActive        BIT               NOT NULL DEFAULT 1,
        CreatedAt       DATETIME2         NOT NULL DEFAULT SYSDATETIME(),
        LastUsedAt      DATETIME2         NULL,
        CreatedByUserID INT               NULL,
        CONSTRAINT FK_ApiKey_Subscriber FOREIGN KEY (SubscriberID)
            REFERENCES dbo.Subscriber(SubscriberID)
    );
END

IF OBJECT_ID('dbo.SignedClipToken','U') IS NULL
BEGIN
    CREATE TABLE dbo.SignedClipToken (
        TokenID         INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        Token           NVARCHAR(64)      NOT NULL UNIQUE,
        ClipPath        NVARCHAR(500)     NOT NULL,
        ExpiresAt       DATETIME2         NOT NULL,
        UsedAt          DATETIME2         NULL,
        CONSTRAINT IX_SignedClipToken_Token UNIQUE (Token)
    );
    CREATE INDEX IX_SignedClipToken_Expires ON dbo.SignedClipToken(ExpiresAt);
END

COMMIT TRANSACTION;
GO
PRINT '027 applied.';
