-- =============================================================================
-- RadioMonitor -- Migration 012: Song + Word detection subscription schema
-- =============================================================================
-- Recreates ClientSubscription (dropped in 007) for the new Subscriber model.
-- Adds ownership + source columns to SongDetection and WordDetection.
-- Creates SongDetectionJob and WordDetectionJob tables.
-- Adds Transcript.FullText for retrospective word search.
--
-- DESIGN DOCS:
--   SONG_DETECTION_DESIGN_BRIEFING.md
--   WORD_DETECTION_DESIGN_BRIEFING.md
--
-- KEY DECISIONS:
--   - ClientSubscription is date-ranged (StartDate + EndDate), consistent
--     with commercials and the billing model (tokens = stations × hours).
--   - Pipeline reload filter: Status='active' AND StartDate <= TODAY
--     AND (EndDate IS NULL OR EndDate >= TODAY)
--   - SubscriptionType values: 'song_track', 'song_title', 'keyword'
--   - StationFilter is a JSON array of StationIDs (NULL = all stations)
--   - Multi-subscriber: N rows written per airing if N subscriptions match
-- =============================================================================

USE RadioMonitor;
GO
SET QUOTED_IDENTIFIER ON;
SET XACT_ABORT ON;
GO
BEGIN TRANSACTION;

-- =============================================================================
-- STEP 1: Recreate ClientSubscription (fresh, Subscriber-bound)
-- =============================================================================
IF OBJECT_ID('dbo.ClientSubscription','U') IS NULL
BEGIN
    CREATE TABLE dbo.ClientSubscription (
        SubscriptionID      BIGINT IDENTITY(1,1)  NOT NULL PRIMARY KEY,
        SubscriberID        INT                   NOT NULL,
        SubscriptionType    NVARCHAR(50)          NOT NULL,
        -- 'song_track'  : TargetValue = Shazam TrackID
        -- 'song_title'  : TargetValue = song title (fuzzy match fallback)
        -- 'keyword'     : TargetValue = keyword or phrase
        TargetValue         NVARCHAR(500)         NOT NULL,
        StationFilter       NVARCHAR(MAX)         NULL,
        -- JSON array of StationIDs e.g. '[4,5,7]', NULL = all stations
        Status              NVARCHAR(20)          NOT NULL
            CONSTRAINT DF_ClientSubscription_Status DEFAULT 'active',
        StartDate           DATE                  NOT NULL,
        EndDate             DATE                  NULL,
        -- NULL EndDate = open-ended (monitor until cancelled)
        Artist              NVARCHAR(200)         NULL,
        -- Stored for display purposes (songs only)
        Title               NVARCHAR(200)         NULL,
        -- Stored for display purposes (songs only)
        CreatedByUserID     INT                   NULL,
        CreatedAt           DATETIME2             NOT NULL
            CONSTRAINT DF_ClientSubscription_CreatedAt DEFAULT SYSDATETIME(),
        UpdatedAt           DATETIME2             NOT NULL
            CONSTRAINT DF_ClientSubscription_UpdatedAt DEFAULT SYSDATETIME(),

        CONSTRAINT FK_ClientSubscription_Subscriber
            FOREIGN KEY (SubscriberID) REFERENCES dbo.Subscriber(SubscriberID),
        CONSTRAINT FK_ClientSubscription_User
            FOREIGN KEY (CreatedByUserID) REFERENCES dbo.[User](UserID),
        CONSTRAINT CK_ClientSubscription_Type CHECK (
            SubscriptionType IN ('song_track','song_title','keyword')),
        CONSTRAINT CK_ClientSubscription_Status CHECK (
            Status IN ('active','paused','withdrawn'))
    );

    CREATE INDEX IX_ClientSubscription_Subscriber
        ON dbo.ClientSubscription(SubscriberID);
    CREATE INDEX IX_ClientSubscription_Active
        ON dbo.ClientSubscription(Status, SubscriptionType, StartDate, EndDate)
        WHERE Status = 'active';
END
GO

-- =============================================================================
-- STEP 2: SongDetection -- add SubscriptionID
-- =============================================================================
IF NOT EXISTS (SELECT 1 FROM sys.columns
               WHERE object_id=OBJECT_ID('dbo.SongDetection') AND name='SubscriptionID')
    ALTER TABLE dbo.SongDetection ADD SubscriptionID BIGINT NULL
        CONSTRAINT FK_SongDetection_Subscription
            FOREIGN KEY REFERENCES dbo.ClientSubscription(SubscriptionID);
GO

-- =============================================================================
-- STEP 3: WordDetection -- add SubscriptionID, DetectionSource, JobID
-- =============================================================================
IF NOT EXISTS (SELECT 1 FROM sys.columns
               WHERE object_id=OBJECT_ID('dbo.WordDetection') AND name='SubscriptionID')
    ALTER TABLE dbo.WordDetection ADD SubscriptionID BIGINT NULL;

IF NOT EXISTS (SELECT 1 FROM sys.columns
               WHERE object_id=OBJECT_ID('dbo.WordDetection') AND name='DetectionSource')
    ALTER TABLE dbo.WordDetection ADD DetectionSource NVARCHAR(20) NOT NULL
        CONSTRAINT DF_WordDetection_DetectionSource DEFAULT 'live';

IF NOT EXISTS (SELECT 1 FROM sys.columns
               WHERE object_id=OBJECT_ID('dbo.WordDetection') AND name='JobID')
    ALTER TABLE dbo.WordDetection ADD JobID BIGINT NULL;
GO

-- =============================================================================
-- STEP 4: Transcript -- add FullText (foundation for retrospective word search)
-- =============================================================================
IF NOT EXISTS (SELECT 1 FROM sys.columns
               WHERE object_id=OBJECT_ID('dbo.Transcript') AND name='FullText')
    ALTER TABLE dbo.Transcript ADD FullText NVARCHAR(MAX) NULL;
GO

-- =============================================================================
-- STEP 5: SongDetectionJob
-- =============================================================================
IF OBJECT_ID('dbo.SongDetectionJob','U') IS NULL
BEGIN
    CREATE TABLE dbo.SongDetectionJob (
        JobID           BIGINT IDENTITY(1,1)  NOT NULL PRIMARY KEY,
        SubscriberID    INT                   NOT NULL,
        SubscriptionID  BIGINT                NULL,
        TrackID         NVARCHAR(100)         NULL,
        ArtistFilter    NVARCHAR(500)         NULL,
        TitleFilter     NVARCHAR(500)         NULL,
        StationFilter   NVARCHAR(MAX)         NULL,
        DateFrom        DATE                  NOT NULL,
        DateTo          DATE                  NOT NULL,
        Status          NVARCHAR(20)          NOT NULL
            CONSTRAINT DF_SongDetectionJob_Status DEFAULT 'pending',
        CreatedAt       DATETIME2             NOT NULL
            CONSTRAINT DF_SongDetectionJob_CreatedAt DEFAULT SYSDATETIME(),
        StartedAt       DATETIME2             NULL,
        CompletedAt     DATETIME2             NULL,
        ResultCount     INT                   NULL,
        ErrorMessage    NVARCHAR(MAX)         NULL,

        CONSTRAINT FK_SongDetectionJob_Subscriber
            FOREIGN KEY (SubscriberID) REFERENCES dbo.Subscriber(SubscriberID),
        CONSTRAINT CK_SongDetectionJob_Status CHECK (
            Status IN ('pending','running','complete','failed'))
    );
    CREATE INDEX IX_SongDetectionJob_Pending
        ON dbo.SongDetectionJob(Status, CreatedAt)
        WHERE Status = 'pending';
END
GO

-- =============================================================================
-- STEP 6: WordDetectionJob
-- =============================================================================
IF OBJECT_ID('dbo.WordDetectionJob','U') IS NULL
BEGIN
    CREATE TABLE dbo.WordDetectionJob (
        JobID           BIGINT IDENTITY(1,1)  NOT NULL PRIMARY KEY,
        SubscriberID    INT                   NOT NULL,
        SubscriptionID  BIGINT                NULL,
        Keyword         NVARCHAR(500)         NOT NULL,
        StationFilter   NVARCHAR(MAX)         NULL,
        DateFrom        DATE                  NOT NULL,
        DateTo          DATE                  NOT NULL,
        Status          NVARCHAR(20)          NOT NULL
            CONSTRAINT DF_WordDetectionJob_Status DEFAULT 'pending',
        CreatedAt       DATETIME2             NOT NULL
            CONSTRAINT DF_WordDetectionJob_CreatedAt DEFAULT SYSDATETIME(),
        StartedAt       DATETIME2             NULL,
        CompletedAt     DATETIME2             NULL,
        ResultCount     INT                   NULL,
        ErrorMessage    NVARCHAR(MAX)         NULL,

        CONSTRAINT FK_WordDetectionJob_Subscriber
            FOREIGN KEY (SubscriberID) REFERENCES dbo.Subscriber(SubscriberID),
        CONSTRAINT CK_WordDetectionJob_Status CHECK (
            Status IN ('pending','running','complete','failed'))
    );
    CREATE INDEX IX_WordDetectionJob_Pending
        ON dbo.WordDetectionJob(Status, CreatedAt)
        WHERE Status = 'pending';
END
GO

-- Add FK on WordDetection.JobID now that WordDetectionJob exists
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name='FK_WordDetection_Job')
BEGIN
    IF EXISTS (SELECT 1 FROM sys.columns
               WHERE object_id=OBJECT_ID('dbo.WordDetection') AND name='JobID')
    BEGIN
        ALTER TABLE dbo.WordDetection ADD
            CONSTRAINT FK_WordDetection_Job
                FOREIGN KEY (JobID) REFERENCES dbo.WordDetectionJob(JobID);
    END
END
GO

COMMIT TRANSACTION;
GO

-- =============================================================================
-- VERIFICATION
-- =============================================================================
PRINT '--- New/modified tables ---';
SELECT name, create_date FROM sys.tables
WHERE name IN ('ClientSubscription','SongDetectionJob','WordDetectionJob')
ORDER BY name;

PRINT '--- SongDetection new column ---';
SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_NAME='SongDetection' AND COLUMN_NAME='SubscriptionID';

PRINT '--- WordDetection new columns ---';
SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_NAME='WordDetection'
  AND COLUMN_NAME IN ('SubscriptionID','DetectionSource','JobID');

PRINT '--- Transcript.FullText ---';
SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_NAME='Transcript' AND COLUMN_NAME='FullText';

PRINT '--- ClientSubscription columns ---';
SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, IS_NULLABLE
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_NAME='ClientSubscription'
ORDER BY ORDINAL_POSITION;

PRINT '012 applied successfully.';
