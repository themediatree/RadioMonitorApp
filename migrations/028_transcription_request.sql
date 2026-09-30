-- Migration 028: TranscriptionRequest
USE RadioMonitor;
GO
SET QUOTED_IDENTIFIER ON; SET XACT_ABORT ON;
GO
BEGIN TRANSACTION;

IF OBJECT_ID('dbo.TranscriptionRequest','U') IS NULL
BEGIN
    CREATE TABLE dbo.TranscriptionRequest (
        RequestID       INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        SubscriberID    INT               NOT NULL,
        UserID          INT               NOT NULL,
        StationID       INT               NOT NULL,
        DateFrom        DATE              NOT NULL,
        DateTo          DATE              NOT NULL,
        TimeFrom        NVARCHAR(8)       NULL,  -- HH:MM
        TimeTo          NVARCHAR(8)       NULL,  -- HH:MM
        OutputFormat    NVARCHAR(16)      NOT NULL DEFAULT 'chunks', -- 'chunks' | 'concatenated'
        Status          NVARCHAR(20)      NOT NULL DEFAULT 'pending', -- 'pending' | 'ready' | 'partial'
        TokensDebited   DECIMAL(18,4)     NOT NULL DEFAULT 0,
        MergedJsonPath  NVARCHAR(500)     NULL,  -- only for concatenated + ready
        ChunkCount      INT               NULL,  -- how many chunks matched
        CreatedAt       DATETIME2         NOT NULL DEFAULT SYSDATETIME(),
        ProcessedAt     DATETIME2         NULL,

        CONSTRAINT FK_TranscriptionRequest_Subscriber
            FOREIGN KEY (SubscriberID) REFERENCES dbo.Subscriber(SubscriberID),
        CONSTRAINT FK_TranscriptionRequest_User
            FOREIGN KEY (UserID) REFERENCES dbo.[User](UserID),
        CONSTRAINT FK_TranscriptionRequest_Station
            FOREIGN KEY (StationID) REFERENCES dbo.Station(StationID),
        CONSTRAINT CK_TranscriptionRequest_Format
            CHECK (OutputFormat IN ('chunks','concatenated')),
        CONSTRAINT CK_TranscriptionRequest_Status
            CHECK (Status IN ('pending','ready','partial'))
    );
END

COMMIT TRANSACTION;
GO
PRINT '028 applied.';
