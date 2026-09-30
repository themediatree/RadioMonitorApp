-- =============================================================================
-- RadioMonitor Web App -- Migration 005: Tenancy expansion
-- =============================================================================
-- Adds the schema needed for the authorization model (AUTHORIZATION.md v0.2):
--
--   1. StationAccount      -- tenant/billing wrapper around the pipeline's
--                             Station table (Station itself is NOT modified).
--   2. Campaign.AgencyID    -- nullable FK; one managing agency per campaign,
--                             or NULL for a self-managed (direct client) campaign.
--   3. User.StationAccountID + expanded CK_User_Type / CK_User_Ownership
--                             to support station_admin / station_user logins.
--   4. SubscriptionPlanConfig feature flags for detection types.
--
-- Deliberately NOT added: Brand table, Product table, brand_* user types,
-- CampaignAgency link table. (Brand is a Commercial.Brand text attribute;
-- one agency per campaign means no link table is needed.)
--
-- Verified against the live schema before writing:
--   - Station PK = StationID (int), name column = StationName
--   - Campaign PK = CampaignID (int), already has ClientID FK
--   - User has UserType (nvarchar 20), ClientID, AgencyID, plus CK_User_Type
--     and CK_User_Ownership constraints
--   - SubscriptionPlanConfig PK = PlanCode (nvarchar 20)
--
-- Transaction-wrapped. If anything fails, ROLLBACK.
-- =============================================================================

USE RadioMonitor;
GO

BEGIN TRANSACTION;
GO

-- =============================================================================
-- PART 1: StationAccount
-- =============================================================================
-- Tenant/billing record for a radio station. References the pipeline's Station.
-- One account per station (UNIQUE on StationID). The pipeline's Station table
-- is never written by the web app; this is the web-app-owned side.
IF OBJECT_ID('dbo.StationAccount', 'U') IS NULL
BEGIN
    CREATE TABLE StationAccount (
        StationAccountID  INT IDENTITY PRIMARY KEY,
        StationID         INT NOT NULL,
        ContactEmail      NVARCHAR(255) NULL,
        ContactPhone      NVARCHAR(50) NULL,
        AccountStatus     NVARCHAR(20) NOT NULL DEFAULT 'active',
        SubscriptionPlan  NVARCHAR(20) NOT NULL,
        SubscriptionStart DATETIME2 NULL,
        SubscriptionEnd   DATETIME2 NULL,
        CreatedAt         DATETIME2 NOT NULL DEFAULT SYSDATETIME(),
        UpdatedAt         DATETIME2 NOT NULL DEFAULT SYSDATETIME(),

        CONSTRAINT FK_StationAccount_Station
            FOREIGN KEY (StationID) REFERENCES Station(StationID),
        CONSTRAINT FK_StationAccount_Plan
            FOREIGN KEY (SubscriptionPlan) REFERENCES SubscriptionPlanConfig(PlanCode),
        CONSTRAINT UQ_StationAccount_Station UNIQUE (StationID),
        CONSTRAINT CK_StationAccount_Status
            CHECK (AccountStatus IN ('active','expired_grace','archived','cancelled'))
    );
END
GO

-- =============================================================================
-- PART 2: Campaign.AgencyID  (one managing agency per campaign, or NULL)
-- =============================================================================
IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID('dbo.Campaign') AND name = 'AgencyID'
)
BEGIN
    ALTER TABLE dbo.Campaign ADD AgencyID INT NULL;
END
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_Campaign_Agency'
)
BEGIN
    ALTER TABLE dbo.Campaign
        ADD CONSTRAINT FK_Campaign_Agency
        FOREIGN KEY (AgencyID) REFERENCES Agency(AgencyID);
END
GO

-- Index to make "campaigns managed by agency A" fast.
IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_Campaign_Agency' AND object_id = OBJECT_ID('dbo.Campaign')
)
BEGIN
    CREATE INDEX IX_Campaign_Agency ON dbo.Campaign(AgencyID) WHERE AgencyID IS NOT NULL;
END
GO

-- =============================================================================
-- PART 3: User -- StationAccountID + expanded constraints
-- =============================================================================
-- 3a. Add the column.
IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID('dbo.[User]') AND name = 'StationAccountID'
)
BEGIN
    ALTER TABLE dbo.[User] ADD StationAccountID INT NULL;
END
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_User_StationAccount'
)
BEGIN
    ALTER TABLE dbo.[User]
        ADD CONSTRAINT FK_User_StationAccount
        FOREIGN KEY (StationAccountID) REFERENCES StationAccount(StationAccountID);
END
GO

-- 3b. Replace CK_User_Type to allow station_admin / station_user.
IF EXISTS (SELECT 1 FROM sys.check_constraints WHERE name = 'CK_User_Type')
    ALTER TABLE dbo.[User] DROP CONSTRAINT CK_User_Type;
GO
ALTER TABLE dbo.[User] ADD CONSTRAINT CK_User_Type CHECK (
    UserType IN (
        'internal',
        'client_admin','client_user',
        'agency_admin','agency_user',
        'station_admin','station_user'
    )
);
GO

-- 3c. Replace CK_User_Ownership so each user type binds to exactly its own org.
--     internal      -> Client/Agency/Station all NULL
--     client_*      -> ClientID set; Agency/Station NULL
--     agency_*      -> AgencyID set; Client/Station NULL
--     station_*     -> StationAccountID set; Client/Agency NULL
IF EXISTS (SELECT 1 FROM sys.check_constraints WHERE name = 'CK_User_Ownership')
    ALTER TABLE dbo.[User] DROP CONSTRAINT CK_User_Ownership;
GO
ALTER TABLE dbo.[User] ADD CONSTRAINT CK_User_Ownership CHECK (
    (UserType = 'internal'
        AND ClientID IS NULL AND AgencyID IS NULL AND StationAccountID IS NULL)
 OR (UserType IN ('client_admin','client_user')
        AND ClientID IS NOT NULL AND AgencyID IS NULL AND StationAccountID IS NULL)
 OR (UserType IN ('agency_admin','agency_user')
        AND AgencyID IS NOT NULL AND ClientID IS NULL AND StationAccountID IS NULL)
 OR (UserType IN ('station_admin','station_user')
        AND StationAccountID IS NOT NULL AND ClientID IS NULL AND AgencyID IS NULL)
);
GO

-- =============================================================================
-- PART 4: SubscriptionPlanConfig -- detection-type feature flags
-- =============================================================================
-- Names provisional (AUTHORIZATION.md §9 open item). Default everything ON so
-- existing plans keep current behaviour; tune per-plan later.
IF NOT EXISTS (SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID('dbo.SubscriptionPlanConfig') AND name = 'AllowSpectrumAnalysis')
    ALTER TABLE dbo.SubscriptionPlanConfig ADD AllowSpectrumAnalysis BIT NOT NULL DEFAULT 1;
GO
IF NOT EXISTS (SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID('dbo.SubscriptionPlanConfig') AND name = 'AllowTranscription')
    ALTER TABLE dbo.SubscriptionPlanConfig ADD AllowTranscription BIT NOT NULL DEFAULT 1;
GO
IF NOT EXISTS (SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID('dbo.SubscriptionPlanConfig') AND name = 'AllowSongDetection')
    ALTER TABLE dbo.SubscriptionPlanConfig ADD AllowSongDetection BIT NOT NULL DEFAULT 1;
GO
IF NOT EXISTS (SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID('dbo.SubscriptionPlanConfig') AND name = 'AllowWordDetection')
    ALTER TABLE dbo.SubscriptionPlanConfig ADD AllowWordDetection BIT NOT NULL DEFAULT 1;
GO

-- =============================================================================
-- COMMIT
-- =============================================================================
COMMIT TRANSACTION;
GO

-- =============================================================================
-- VERIFICATION (run after commit; all should return expected results)
-- =============================================================================
-- StationAccount exists with its columns
SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_NAME = 'StationAccount' ORDER BY ORDINAL_POSITION;

-- Campaign now has AgencyID + FK
SELECT c.name AS ColumnName FROM sys.columns c
WHERE c.object_id = OBJECT_ID('dbo.Campaign') AND c.name = 'AgencyID';
SELECT name FROM sys.foreign_keys WHERE name = 'FK_Campaign_Agency';

-- User has StationAccountID and the two refreshed constraints
SELECT c.name FROM sys.columns c
WHERE c.object_id = OBJECT_ID('dbo.[User]') AND c.name = 'StationAccountID';
SELECT definition FROM sys.check_constraints WHERE name = 'CK_User_Type';
SELECT definition FROM sys.check_constraints WHERE name = 'CK_User_Ownership';

-- Plan feature flags present
SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_NAME = 'SubscriptionPlanConfig'
  AND COLUMN_NAME IN ('AllowSpectrumAnalysis','AllowTranscription',
                      'AllowSongDetection','AllowWordDetection')
ORDER BY COLUMN_NAME;

-- Confirm no brand_* leaked into the type constraint (should return the 7 valid types only)
-- and that existing data still satisfies the new ownership constraint:
SELECT UserType, COUNT(*) AS Cnt FROM dbo.[User] GROUP BY UserType;
