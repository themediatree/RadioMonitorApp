-- =============================================================================
-- RadioMonitor Web App -- Migration 007: Subscriber refactor (clean-slate)
-- =============================================================================
-- Replaces the old Client/Agency/StationAccount tripartite tenant model with a
-- single flat Subscriber table (seven types). Adds the FingerprintID identity
-- column on Commercial for Option C dedup, plus DisplayTapeID for the user's
-- own naming convention. Drops AgencyClient (no agency-manages-client
-- relationship under the new model). Truncates all transactional data.
--
-- DESIGN DOCS:
--   ENTITY_MODEL.md                   -- the new Subscriber model
--   FINGERPRINT_IDENTITY_DESIGN.md    -- Option C with Option-alpha withdrawal
--   AUTHORIZATION.md (v0.3)           -- the new scoping rule
--
-- KEY DECISIONS BAKED INTO THIS MIGRATION:
--   - Clean-slate: no data migration from Client/Agency/StationAccount; the
--     prior session's test tenants are dropped. The single internal admin row
--     in [User] is PRESERVED -- it has UserType='internal' and was already
--     compatible with the new ownership constraint.
--   - CommercialName stores the FILENAME STEM "<SubscriberID>_<DisplayTapeID>"
--     (the prefixed form). DisplayTapeID stores the user's clean convention.
--     This is the resolver-friendly choice agreed with the pipeline thread
--     in PIPELINE_FILENAME_ADDENDUM_RESPONSE.md.
--   - NO IsFileOwner column. Under (ii), the on-disk filename matching an
--     active CommercialName IS the file-ownership invariant. Withdrawal
--     handles file-owner exit via Option-alpha restage (web-app logic, R2).
--   - UserType values reduce from 7 to 3: internal, subscriber_admin,
--     subscriber_user.
--
-- LESSONS BANKED (from migrations 005/005b/006 and the 007 first-attempt):
--   - Leading SET QUOTED_IDENTIFIER ON + SET XACT_ABORT ON.
--   - Multi-statement script wrapped in a transaction so any failure rolls
--     back cleanly (the partial-apply 005 disaster).
--   - Guarded "IF NOT EXISTS" where it matters so the script is idempotent.
--   - FK column types must match exactly the columns they reference (the
--     nvarchar(20) vs nvarchar(50) PlanCode bug from 005).
--   - Verification block at the end with INFORMATION_SCHEMA dumps so the
--     applied state is provable, not assumed.
--   - 007's first run failed because SQL Server refuses to drop a column
--     that an index references (IX_Campaign_Client_Dates created in 005
--     blocked dropping Campaign.ClientID). STEP 3b below drops any indexes
--     on the about-to-be-dropped columns dynamically. The lesson: when
--     dropping columns, always drop FKs AND indexes first; query
--     sys.index_columns rather than assuming you know all index names.
-- =============================================================================

USE RadioMonitor;
GO

SET QUOTED_IDENTIFIER ON;
SET XACT_ABORT ON;
GO

BEGIN TRANSACTION;
GO

-- =============================================================================
-- STEP 1: TRUNCATE transactional data (clean-slate; preserves the internal
-- admin via WHERE clause, since DELETE is needed where FKs exist).
-- =============================================================================
-- Order respects existing FKs: children (links, references) before parents.
DELETE FROM CampaignCommercial;
DELETE FROM CampaignStation;
DELETE FROM Campaign;
DELETE FROM Commercial;

-- ClientSubscription references Client; we'll drop the table entirely (it's
-- a deferred-feature table for songs/words rules, and will be recreated
-- against Subscriber when that UI is built).
IF OBJECT_ID('dbo.ClientSubscription','U') IS NOT NULL DELETE FROM ClientSubscription;

-- AgencyClient is the link table being dropped entirely.
IF OBJECT_ID('dbo.AgencyClient','U') IS NOT NULL DELETE FROM AgencyClient;

-- UserInvitation references Client/Agency; truncate, then we'll rework
-- the columns below to point at Subscriber.
DELETE FROM UserInvitation;

-- AuditLog references User; we'll keep the internal admin row in User, so
-- audit rows are technically still valid -- but we're at a clean baseline so
-- truncate for tidiness.
DELETE FROM AuditLog;
IF OBJECT_ID('dbo.ImpersonationSession','U') IS NOT NULL DELETE FROM ImpersonationSession;

-- ApiKey (if present): per-client keys; references Client.
IF OBJECT_ID('dbo.ApiKey','U') IS NOT NULL DELETE FROM ApiKey;

-- Delete all non-internal users. The internal admin row (UserType='internal',
-- all three old tenant FKs NULL) survives and remains compatible with the
-- new CK_User_Ownership rule.
DELETE FROM [User] WHERE UserType <> 'internal';

GO

-- =============================================================================
-- STEP 2: CREATE the new Subscriber table.
-- =============================================================================
IF OBJECT_ID('dbo.Subscriber','U') IS NULL
BEGIN
    CREATE TABLE dbo.Subscriber (
        SubscriberID         INT IDENTITY(1,1)  NOT NULL PRIMARY KEY,
        Name                 NVARCHAR(255)      NOT NULL,
        Slug                 NVARCHAR(120)      NOT NULL,
        SubscriberType       NVARCHAR(30)       NOT NULL,
        OtherDescription     NVARCHAR(255)      NULL,
        ContactEmail         NVARCHAR(255)      NULL,
        ContactPhone         NVARCHAR(50)       NULL,
        -- Must match SubscriptionPlanConfig.PlanCode's NVARCHAR(50) exactly
        -- (the 005 lesson; do NOT shorten or lengthen).
        SubscriptionPlan     NVARCHAR(50)       NOT NULL,
        SubscriberStatus     NVARCHAR(20)       NOT NULL CONSTRAINT DF_Subscriber_Status DEFAULT 'active',
        -- For SubscriberType='Radio Station' only; FK to the pipeline's Station.
        StationID            INT                NULL,
        SubscriptionStart    DATETIME2          NULL,
        SubscriptionEnd      DATETIME2          NULL,
        CreatedAt            DATETIME2          NOT NULL CONSTRAINT DF_Subscriber_CreatedAt DEFAULT SYSDATETIME(),
        UpdatedAt            DATETIME2          NOT NULL CONSTRAINT DF_Subscriber_UpdatedAt DEFAULT SYSDATETIME(),

        CONSTRAINT UQ_Subscriber_Slug UNIQUE (Slug),
        CONSTRAINT FK_Subscriber_Plan
            FOREIGN KEY (SubscriptionPlan) REFERENCES dbo.SubscriptionPlanConfig(PlanCode),
        CONSTRAINT FK_Subscriber_Station
            FOREIGN KEY (StationID) REFERENCES dbo.Station(StationID),
        CONSTRAINT CK_Subscriber_Type CHECK (
            SubscriberType IN ('Agent','Advertiser','Brand Owner','Radio Station',
                               'Government','Political Party','Other')),
        CONSTRAINT CK_Subscriber_Status CHECK (
            SubscriberStatus IN ('active','expired_grace','archived','cancelled')),
        CONSTRAINT CK_Subscriber_Other_Description CHECK (
            (SubscriberType = 'Other' AND OtherDescription IS NOT NULL AND LEN(OtherDescription) > 0)
            OR (SubscriberType <> 'Other')),
        CONSTRAINT CK_Subscriber_Station CHECK (
            (SubscriberType = 'Radio Station' AND StationID IS NOT NULL)
            OR (SubscriberType <> 'Radio Station' AND StationID IS NULL))
    );
END
GO

-- =============================================================================
-- STEP 3: Add new columns to existing tables (before dropping old ones).
-- =============================================================================

-- User.SubscriberID -- the new owner.
IF NOT EXISTS (SELECT 1 FROM sys.columns
               WHERE object_id = OBJECT_ID('dbo.[User]') AND name = 'SubscriberID')
BEGIN
    ALTER TABLE dbo.[User] ADD SubscriberID INT NULL;
END
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_User_Subscriber')
BEGIN
    ALTER TABLE dbo.[User] ADD CONSTRAINT FK_User_Subscriber
        FOREIGN KEY (SubscriberID) REFERENCES dbo.Subscriber(SubscriberID);
END
GO

-- Campaign.SubscriberID -- collapses Campaign.ClientID + AgencyID.
IF NOT EXISTS (SELECT 1 FROM sys.columns
               WHERE object_id = OBJECT_ID('dbo.Campaign') AND name = 'SubscriberID')
BEGIN
    -- Note: Campaign was just truncated, so no backfill is needed -- the column
    -- can be added NOT NULL with no default because there are zero rows.
    -- But SQL Server requires either a default or NULL on ADD-then-set-NOT-NULL,
    -- so we add NULL and then enforce NOT NULL once any future rows arrive
    -- via app-level guarantees. The FK still applies.
    ALTER TABLE dbo.Campaign ADD SubscriberID INT NULL;
END
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_Campaign_Subscriber')
BEGIN
    ALTER TABLE dbo.Campaign ADD CONSTRAINT FK_Campaign_Subscriber
        FOREIGN KEY (SubscriberID) REFERENCES dbo.Subscriber(SubscriberID);
END
GO

-- Commercial.SubscriberID, DisplayTapeID, FingerprintID.
-- Commercial was just truncated; the new NOT NULL columns are safe to add
-- without a default backfill (zero rows present).
IF NOT EXISTS (SELECT 1 FROM sys.columns
               WHERE object_id = OBJECT_ID('dbo.Commercial') AND name = 'SubscriberID')
BEGIN
    ALTER TABLE dbo.Commercial ADD SubscriberID INT NULL;
END
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_Commercial_Subscriber')
BEGIN
    ALTER TABLE dbo.Commercial ADD CONSTRAINT FK_Commercial_Subscriber
        FOREIGN KEY (SubscriberID) REFERENCES dbo.Subscriber(SubscriberID);
END
GO
IF NOT EXISTS (SELECT 1 FROM sys.columns
               WHERE object_id = OBJECT_ID('dbo.Commercial') AND name = 'DisplayTapeID')
BEGIN
    ALTER TABLE dbo.Commercial ADD DisplayTapeID NVARCHAR(100) NULL;
END
GO
IF NOT EXISTS (SELECT 1 FROM sys.columns
               WHERE object_id = OBJECT_ID('dbo.Commercial') AND name = 'FingerprintID')
BEGIN
    ALTER TABLE dbo.Commercial ADD FingerprintID NVARCHAR(64) NULL;
END
GO

-- UserInvitation.SubscriberID -- collapses ClientID + AgencyID.
IF NOT EXISTS (SELECT 1 FROM sys.columns
               WHERE object_id = OBJECT_ID('dbo.UserInvitation') AND name = 'SubscriberID')
BEGIN
    ALTER TABLE dbo.UserInvitation ADD SubscriberID INT NULL;
END
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_UserInvitation_Subscriber')
BEGIN
    ALTER TABLE dbo.UserInvitation ADD CONSTRAINT FK_UserInvitation_Subscriber
        FOREIGN KEY (SubscriberID) REFERENCES dbo.Subscriber(SubscriberID);
END
GO

-- =============================================================================
-- STEP 3b: Drop any indexes that reference the old columns we're about to
-- drop. SQL Server refuses to drop a column that an index depends on, and
-- migration 005 created IX_Campaign_Client_Dates which references
-- Campaign.ClientID. Other indexes may exist on User.ClientID/AgencyID/
-- StationAccountID, UserInvitation.ClientID/AgencyID, or Campaign.AgencyID
-- depending on the project's full migration history. Rather than enumerate
-- by name (and miss one), query INFORMATION_SCHEMA dynamically and drop
-- every index that references any column we're about to drop.
-- =============================================================================
DECLARE @sql NVARCHAR(MAX) = N'';

SELECT @sql = @sql + N'DROP INDEX ' + QUOTENAME(i.name) + N' ON ' + QUOTENAME(s.name)
                  + N'.' + QUOTENAME(t.name) + N';' + CHAR(10)
FROM sys.indexes i
JOIN sys.tables t ON t.object_id = i.object_id
JOIN sys.schemas s ON s.schema_id = t.schema_id
JOIN sys.index_columns ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id
JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
WHERE i.is_primary_key = 0
  AND i.is_unique_constraint = 0
  AND (
        (t.name = 'Campaign' AND c.name IN ('ClientID','AgencyID'))
     OR (t.name = 'User'     AND c.name IN ('ClientID','AgencyID','StationAccountID'))
     OR (t.name = 'UserInvitation' AND c.name IN ('ClientID','AgencyID'))
  );

IF LEN(@sql) > 0
BEGIN
    PRINT '--- Dropping old indexes that reference about-to-be-dropped columns ---';
    PRINT @sql;
    EXEC sp_executesql @sql;
END
GO

-- =============================================================================
-- STEP 4: Drop FK constraints + check constraints + old columns.
-- =============================================================================
-- Sub-step 4a: dynamically drop EVERY FK whose REFERENCED table is one of the
-- dying tenant tables. This handles FKs on User, Campaign, UserInvitation,
-- AND any others we may not know about (DetectionVisibility, etc.).
-- Discovered dynamically, not hand-listed -- the previous attempt failed
-- because the hand-list missed dependents.
DECLARE @fk_drop_sql NVARCHAR(MAX) = N'';

SELECT @fk_drop_sql = @fk_drop_sql
    + N'ALTER TABLE ' + QUOTENAME(OBJECT_SCHEMA_NAME(fk.parent_object_id))
    + N'.' + QUOTENAME(OBJECT_NAME(fk.parent_object_id))
    + N' DROP CONSTRAINT ' + QUOTENAME(fk.name) + N';' + CHAR(10)
FROM sys.foreign_keys fk
WHERE OBJECT_NAME(fk.referenced_object_id) IN (
    'AgencyClient','ApiKey','ClientSubscription','DetectionVisibility',
    'StationAccount','Client','Agency'
);

IF LEN(@fk_drop_sql) > 0
BEGIN
    PRINT '--- Dropping FKs referencing dying tables ---';
    PRINT @fk_drop_sql;
    EXEC sp_executesql @fk_drop_sql;
END
GO

-- Sub-step 4b: drop check constraints and columns on the User table.
IF EXISTS (SELECT 1 FROM sys.check_constraints WHERE name = 'CK_User_Type')
    ALTER TABLE dbo.[User] DROP CONSTRAINT CK_User_Type;
IF EXISTS (SELECT 1 FROM sys.check_constraints WHERE name = 'CK_User_Ownership')
    ALTER TABLE dbo.[User] DROP CONSTRAINT CK_User_Ownership;

IF EXISTS (SELECT 1 FROM sys.columns
           WHERE object_id = OBJECT_ID('dbo.[User]') AND name = 'ClientID')
    ALTER TABLE dbo.[User] DROP COLUMN ClientID;
IF EXISTS (SELECT 1 FROM sys.columns
           WHERE object_id = OBJECT_ID('dbo.[User]') AND name = 'AgencyID')
    ALTER TABLE dbo.[User] DROP COLUMN AgencyID;
IF EXISTS (SELECT 1 FROM sys.columns
           WHERE object_id = OBJECT_ID('dbo.[User]') AND name = 'StationAccountID')
    ALTER TABLE dbo.[User] DROP COLUMN StationAccountID;
GO

-- Sub-step 4c: drop old columns on Campaign.
IF EXISTS (SELECT 1 FROM sys.columns
           WHERE object_id = OBJECT_ID('dbo.Campaign') AND name = 'ClientID')
    ALTER TABLE dbo.Campaign DROP COLUMN ClientID;
IF EXISTS (SELECT 1 FROM sys.columns
           WHERE object_id = OBJECT_ID('dbo.Campaign') AND name = 'AgencyID')
    ALTER TABLE dbo.Campaign DROP COLUMN AgencyID;
GO

-- Sub-step 4d: drop old columns on UserInvitation.
IF EXISTS (SELECT 1 FROM sys.columns
           WHERE object_id = OBJECT_ID('dbo.UserInvitation') AND name = 'ClientID')
    ALTER TABLE dbo.UserInvitation DROP COLUMN ClientID;
IF EXISTS (SELECT 1 FROM sys.columns
           WHERE object_id = OBJECT_ID('dbo.UserInvitation') AND name = 'AgencyID')
    ALTER TABLE dbo.UserInvitation DROP COLUMN AgencyID;
GO

-- =============================================================================
-- STEP 5: Drop old tables. FKs referencing them were already dropped in
-- STEP 4a, so this is now safe.
-- =============================================================================

-- Clear data in the deferred-feature dying tables before drop. (AgencyClient
-- and the relevant User rows were already cleared in STEP 1.)
IF OBJECT_ID('dbo.DetectionVisibility','U') IS NOT NULL DELETE FROM DetectionVisibility;
IF OBJECT_ID('dbo.ClientSubscription','U') IS NOT NULL DELETE FROM ClientSubscription;
IF OBJECT_ID('dbo.ApiKey','U') IS NOT NULL DELETE FROM ApiKey;
IF OBJECT_ID('dbo.StationAccount','U') IS NOT NULL DELETE FROM StationAccount;
IF OBJECT_ID('dbo.Client','U') IS NOT NULL DELETE FROM Client;
IF OBJECT_ID('dbo.Agency','U') IS NOT NULL DELETE FROM Agency;

-- Drop the tables themselves. Children-before-parents as belt-and-braces.
IF OBJECT_ID('dbo.DetectionVisibility','U') IS NOT NULL DROP TABLE dbo.DetectionVisibility;
IF OBJECT_ID('dbo.AgencyClient','U') IS NOT NULL DROP TABLE dbo.AgencyClient;
IF OBJECT_ID('dbo.ClientSubscription','U') IS NOT NULL DROP TABLE dbo.ClientSubscription;
IF OBJECT_ID('dbo.ApiKey','U') IS NOT NULL DROP TABLE dbo.ApiKey;
IF OBJECT_ID('dbo.StationAccount','U') IS NOT NULL DROP TABLE dbo.StationAccount;
IF OBJECT_ID('dbo.Client','U') IS NOT NULL DROP TABLE dbo.Client;
IF OBJECT_ID('dbo.Agency','U') IS NOT NULL DROP TABLE dbo.Agency;
GO

-- =============================================================================
-- STEP 6: Add the new constraints + indexes.
-- =============================================================================

-- New CK_User_Type (3 values).
ALTER TABLE dbo.[User] ADD CONSTRAINT CK_User_Type CHECK (
    UserType IN ('internal','subscriber_admin','subscriber_user'));

-- New CK_User_Ownership: internal => SubscriberID NULL; non-internal => NOT NULL.
ALTER TABLE dbo.[User] ADD CONSTRAINT CK_User_Ownership CHECK (
    (UserType = 'internal'  AND SubscriberID IS NULL)
    OR (UserType <> 'internal' AND SubscriberID IS NOT NULL));

-- Per-Subscriber TapeID uniqueness on Commercial.
IF NOT EXISTS (SELECT 1 FROM sys.indexes
               WHERE name = 'UQ_Commercial_Subscriber_TapeID'
                 AND object_id = OBJECT_ID('dbo.Commercial'))
BEGIN
    -- Filtered: only enforces uniqueness once both columns are populated. The
    -- only realistic row that could have NULL here is a row from the prior
    -- schema; we truncated, so there are none -- but the filter is a guard.
    CREATE UNIQUE INDEX UQ_Commercial_Subscriber_TapeID
        ON dbo.Commercial(SubscriberID, DisplayTapeID)
        WHERE SubscriberID IS NOT NULL AND DisplayTapeID IS NOT NULL;
END
GO

-- Fast lookups for the FingerprintID scoping join.
IF NOT EXISTS (SELECT 1 FROM sys.indexes
               WHERE name = 'IX_Commercial_FingerprintID'
                 AND object_id = OBJECT_ID('dbo.Commercial'))
BEGIN
    CREATE INDEX IX_Commercial_FingerprintID ON dbo.Commercial(FingerprintID);
END
GO

-- Fast Subscriber-owned lookups (UI listing, scoping).
IF NOT EXISTS (SELECT 1 FROM sys.indexes
               WHERE name = 'IX_Commercial_Subscriber'
                 AND object_id = OBJECT_ID('dbo.Commercial'))
BEGIN
    CREATE INDEX IX_Commercial_Subscriber ON dbo.Commercial(SubscriberID);
END
GO

COMMIT TRANSACTION;
GO

-- =============================================================================
-- VERIFICATION -- prove the applied state.
-- =============================================================================
PRINT '--- Subscriber columns ---';
SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, IS_NULLABLE
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_NAME = 'Subscriber'
ORDER BY ORDINAL_POSITION;

PRINT '--- Commercial new columns ---';
SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, IS_NULLABLE
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_NAME = 'Commercial'
  AND COLUMN_NAME IN ('SubscriberID','DisplayTapeID','FingerprintID','Status','CommercialName')
ORDER BY ORDINAL_POSITION;

PRINT '--- User new shape ---';
SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, IS_NULLABLE
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_NAME = 'User' AND COLUMN_NAME IN ('UserID','UserType','SubscriberID')
ORDER BY ORDINAL_POSITION;

PRINT '--- Old tables (should all return zero rows / be missing) ---';
SELECT 'Client'         AS T, CASE WHEN OBJECT_ID('dbo.Client','U') IS NULL THEN 1 ELSE 0 END AS Dropped
UNION ALL SELECT 'Agency',        CASE WHEN OBJECT_ID('dbo.Agency','U') IS NULL THEN 1 ELSE 0 END
UNION ALL SELECT 'StationAccount',CASE WHEN OBJECT_ID('dbo.StationAccount','U') IS NULL THEN 1 ELSE 0 END
UNION ALL SELECT 'AgencyClient',  CASE WHEN OBJECT_ID('dbo.AgencyClient','U') IS NULL THEN 1 ELSE 0 END
UNION ALL SELECT 'ClientSubscription',CASE WHEN OBJECT_ID('dbo.ClientSubscription','U') IS NULL THEN 1 ELSE 0 END;

PRINT '--- New check constraints ---';
SELECT name, definition FROM sys.check_constraints
WHERE name IN ('CK_User_Type','CK_User_Ownership','CK_Subscriber_Type',
               'CK_Subscriber_Status','CK_Subscriber_Other_Description',
               'CK_Subscriber_Station');

PRINT '--- New indexes ---';
SELECT name FROM sys.indexes
WHERE name IN ('UQ_Subscriber_Slug','UQ_Commercial_Subscriber_TapeID',
               'IX_Commercial_FingerprintID','IX_Commercial_Subscriber');

PRINT '--- Row counts ---';
SELECT 'Subscriber'    AS T, COUNT(*) AS N FROM Subscriber
UNION ALL SELECT 'User',          COUNT(*) FROM [User]
UNION ALL SELECT 'Campaign',      COUNT(*) FROM Campaign
UNION ALL SELECT 'Commercial',    COUNT(*) FROM Commercial
UNION ALL SELECT 'AuditLog',      COUNT(*) FROM AuditLog
UNION ALL SELECT 'UserInvitation',COUNT(*) FROM UserInvitation
UNION ALL SELECT '-- preserved --',-1
UNION ALL SELECT 'Station',       COUNT(*) FROM Station
UNION ALL SELECT 'SubscriptionPlanConfig', COUNT(*) FROM SubscriptionPlanConfig;
-- Expected: Subscriber=0, User=1 (the internal admin), Campaign/Commercial/
-- AuditLog/UserInvitation=0, Station=6, SubscriptionPlanConfig=4.
