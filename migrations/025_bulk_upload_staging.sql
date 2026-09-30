-- =============================================================================
-- RadioMonitor -- Migration 025: Bulk upload staging tables
-- =============================================================================
-- Two-step flow: validate (parse workbook + ZIP, check every row, compute
-- total cost) -> commit (process only the rows that passed validation,
-- through the same service functions the single-entry forms use).
--
-- BulkUploadSession bridges the two requests. Short-lived (expires after
-- 1 hour) -- not meant as permanent storage, just enough to survive the
-- validate -> review -> commit round trip.
-- =============================================================================

USE RadioMonitor;
GO
SET QUOTED_IDENTIFIER ON;
SET XACT_ABORT ON;
GO
BEGIN TRANSACTION;

IF OBJECT_ID('dbo.BulkUploadSession','U') IS NULL
BEGIN
    CREATE TABLE dbo.BulkUploadSession (
        SessionID       INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        SessionToken    NVARCHAR(64)      NOT NULL UNIQUE,
        UserID          INT               NOT NULL,
        SubscriberID    INT               NULL,
        Status          NVARCHAR(20)      NOT NULL
            CONSTRAINT DF_BulkUploadSession_Status DEFAULT 'validated',
        TotalCost       DECIMAL(18,4)     NOT NULL DEFAULT 0,
        ValidRowCount   INT               NOT NULL DEFAULT 0,
        FailedRowCount  INT               NOT NULL DEFAULT 0,
        ZipStoragePath  NVARCHAR(500)     NULL,
        CreatedAt       DATETIME2         NOT NULL
            CONSTRAINT DF_BulkUploadSession_CreatedAt DEFAULT SYSDATETIME(),
        ExpiresAt       DATETIME2         NOT NULL,
        CommittedAt     DATETIME2         NULL,

        CONSTRAINT FK_BulkUploadSession_User FOREIGN KEY (UserID) REFERENCES dbo.[User](UserID),
        CONSTRAINT CK_BulkUploadSession_Status CHECK (
            Status IN ('validated','committed','expired'))
    );
END

IF OBJECT_ID('dbo.BulkUploadRow','U') IS NULL
BEGIN
    CREATE TABLE dbo.BulkUploadRow (
        RowID           INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        SessionID       INT               NOT NULL,
        SheetName       NVARCHAR(30)      NOT NULL,
        RowNumber       INT               NOT NULL,
        IsValid         BIT               NOT NULL,
        ErrorMessage    NVARCHAR(500)     NULL,
        RowDataJson     NVARCHAR(MAX)     NOT NULL,
        EstimatedCost   DECIMAL(18,4)     NULL,
        ProcessedAt     DATETIME2         NULL,
        ResultMessage   NVARCHAR(500)     NULL,

        CONSTRAINT FK_BulkUploadRow_Session
            FOREIGN KEY (SessionID) REFERENCES dbo.BulkUploadSession(SessionID)
    );

    CREATE INDEX IX_BulkUploadRow_Session ON dbo.BulkUploadRow(SessionID);
END

COMMIT TRANSACTION;
GO

PRINT '--- Bulk upload tables ---';
SELECT name FROM sys.tables WHERE name IN ('BulkUploadSession','BulkUploadRow');

PRINT '025 applied.';
