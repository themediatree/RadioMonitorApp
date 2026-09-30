-- =============================================================================
-- RadioMonitor Web App -- Migration 005b: finish tenancy expansion
-- =============================================================================
-- 005 partially applied: Campaign.AgencyID, User.StationAccountID, the refreshed
-- CK_User_Type / CK_User_Ownership constraints, and the four plan feature flags
-- all succeeded. Three things FAILED and are completed here:
--
--   1. StationAccount table -- failed because SubscriptionPlan was NVARCHAR(20)
--      but PlanCode is NVARCHAR(50). FK requires exact length match. FIXED to 50.
--   2. FK_User_StationAccount -- failed only because StationAccount didn't exist
--      yet. Created here once the table exists.
--   3. IX_Campaign_Agency -- failed because the FILTERED index needs
--      QUOTED_IDENTIFIER ON. Replaced with a plain (non-filtered) index to avoid
--      SET-option fragility under sqlcmd.
--
-- Two safety changes vs 005 (lessons learned):
--   * SET XACT_ABORT ON  -- ANY error now aborts and rolls back the whole
--     transaction automatically (005 left a partial state because GO batches
--     don't auto-rollback without this).
--   * SET QUOTED_IDENTIFIER ON -- correct SET options for index creation.
--
-- Every step is guarded (IF NOT EXISTS), so this is safe to re-run.
-- =============================================================================

USE RadioMonitor;
GO

SET XACT_ABORT ON;
SET QUOTED_IDENTIFIER ON;
GO

BEGIN TRANSACTION;
GO

-- =============================================================================
-- PART 1: StationAccount (corrected: SubscriptionPlan = NVARCHAR(50))
-- =============================================================================
IF OBJECT_ID('dbo.StationAccount', 'U') IS NULL
BEGIN
    CREATE TABLE StationAccount (
        StationAccountID  INT IDENTITY PRIMARY KEY,
        StationID         INT NOT NULL,
        ContactEmail      NVARCHAR(255) NULL,
        ContactPhone      NVARCHAR(50) NULL,
        AccountStatus     NVARCHAR(20) NOT NULL DEFAULT 'active',
        SubscriptionPlan  NVARCHAR(50) NOT NULL,      -- matches PlanCode (50)
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
-- PART 2: FK_User_StationAccount (now that StationAccount exists)
-- =============================================================================
-- The column User.StationAccountID already exists (005 added it successfully).
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_User_StationAccount')
BEGIN
    ALTER TABLE dbo.[User]
        ADD CONSTRAINT FK_User_StationAccount
        FOREIGN KEY (StationAccountID) REFERENCES StationAccount(StationAccountID);
END
GO

-- =============================================================================
-- PART 3: IX_Campaign_Agency (plain index, not filtered)
-- =============================================================================
IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_Campaign_Agency' AND object_id = OBJECT_ID('dbo.Campaign')
)
BEGIN
    CREATE INDEX IX_Campaign_Agency ON dbo.Campaign(AgencyID);
END
GO

-- =============================================================================
-- COMMIT
-- =============================================================================
COMMIT TRANSACTION;
GO

-- =============================================================================
-- VERIFICATION
-- =============================================================================
-- StationAccount now exists with all columns
SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_NAME = 'StationAccount' ORDER BY ORDINAL_POSITION;

-- Its FKs and constraints exist
SELECT name FROM sys.foreign_keys
WHERE name IN ('FK_StationAccount_Station','FK_StationAccount_Plan','FK_User_StationAccount')
ORDER BY name;

-- The Campaign index exists
SELECT name FROM sys.indexes WHERE name = 'IX_Campaign_Agency';

-- Full sanity: confirm the complete set of new objects from 005 + 005b
SELECT 'Campaign.AgencyID' AS Item, COUNT(*) AS Present FROM sys.columns
  WHERE object_id = OBJECT_ID('dbo.Campaign') AND name = 'AgencyID'
UNION ALL SELECT 'User.StationAccountID', COUNT(*) FROM sys.columns
  WHERE object_id = OBJECT_ID('dbo.[User]') AND name = 'StationAccountID'
UNION ALL SELECT 'StationAccount table', COUNT(*) FROM sys.tables
  WHERE name = 'StationAccount'
UNION ALL SELECT 'FK_Campaign_Agency', COUNT(*) FROM sys.foreign_keys
  WHERE name = 'FK_Campaign_Agency'
UNION ALL SELECT 'FK_User_StationAccount', COUNT(*) FROM sys.foreign_keys
  WHERE name = 'FK_User_StationAccount'
UNION ALL SELECT 'plan flag AllowSongDetection', COUNT(*) FROM sys.columns
  WHERE object_id = OBJECT_ID('dbo.SubscriptionPlanConfig') AND name = 'AllowSongDetection';
-- Every row's Present column should be 1.
