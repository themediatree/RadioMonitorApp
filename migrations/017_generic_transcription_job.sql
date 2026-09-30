-- =============================================================================
-- RadioMonitor -- Migration 017: Generic commercial transcription job
-- =============================================================================
-- Web app inserts a row when a generic commercial is registered (only if no
-- transcript already exists for that FingerprintID -- dedup enforced here,
-- per pipeline's confirmation that generic_transcriber.py does not check).
--
-- Pipeline polls WHERE Status='pending' every 10s, atomic claim.
-- Input:  D:\RadioMonitor\audio_archive\<FingerprintID>.mp3 (UNC, no staging)
-- Output: D:\RadioMonitor\generic_transcripts\<FingerprintID>.json + .txt
--
-- Contract confirmed with pipeline thread -- see GENERIC_TRANSCRIPTION
-- design discussion.
-- =============================================================================

USE RadioMonitor;
GO
SET QUOTED_IDENTIFIER ON;
SET XACT_ABORT ON;
GO
BEGIN TRANSACTION;

IF OBJECT_ID('dbo.GenericTranscriptionJob','U') IS NULL
BEGIN
    CREATE TABLE dbo.GenericTranscriptionJob (
        JobID           BIGINT IDENTITY(1,1)  NOT NULL PRIMARY KEY,
        FingerprintID   NVARCHAR(64)          NOT NULL,
        CommercialID    INT                   NULL,
        -- The Commercial that triggered this job (file-owner). Informational
        -- only -- the transcript is keyed by FingerprintID, reused by all
        -- siblings sharing the same audio.
        Status          NVARCHAR(20)          NOT NULL
            CONSTRAINT DF_GenericTranscriptionJob_Status DEFAULT 'pending',
        JsonPath        NVARCHAR(500)         NULL,
        TextPath        NVARCHAR(500)         NULL,
        CreatedAt       DATETIME2             NOT NULL
            CONSTRAINT DF_GenericTranscriptionJob_CreatedAt DEFAULT SYSDATETIME(),
        StartedAt       DATETIME2             NULL,
        CompletedAt     DATETIME2             NULL,
        ErrorMessage    NVARCHAR(MAX)         NULL,

        CONSTRAINT FK_GenericTranscriptionJob_Commercial
            FOREIGN KEY (CommercialID) REFERENCES dbo.Commercial(CommercialID),
        CONSTRAINT CK_GenericTranscriptionJob_Status CHECK (
            Status IN ('pending','running','complete','failed')),
        -- Dedup: one job per FingerprintID, ever. Web app checks this before
        -- inserting; this unique constraint is the backstop.
        CONSTRAINT UQ_GenericTranscriptionJob_FingerprintID UNIQUE (FingerprintID)
    );

    CREATE INDEX IX_GenericTranscriptionJob_Pending
        ON dbo.GenericTranscriptionJob(Status, CreatedAt)
        WHERE Status = 'pending';
END
GO

COMMIT TRANSACTION;
GO

PRINT '--- GenericTranscriptionJob ---';
SELECT name, create_date FROM sys.tables WHERE name = 'GenericTranscriptionJob';

PRINT '017 applied.';
