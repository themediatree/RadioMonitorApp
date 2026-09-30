-- =============================================================================
-- RadioMonitor Web App -- Migration 006: Commercial.Status (withdrawal lifecycle)
-- =============================================================================
-- Adds a lifecycle status to Commercial so the pipeline's nightly library-
-- manager can include only live commercials, and the web app can withdraw a
-- commercial without deleting its file (audit-preserving). Agreed with the
-- pipeline thread (PIPELINE_REGISTRATION_ADDENDUM_RESPONSE, Option B-2).
--
--   Commercial.Status NVARCHAR(20) NOT NULL DEFAULT 'active'
--     allowed: 'active' | 'pending' | 'withdrawn'
--
-- Pipeline library-manager will include a reference iff:
--     SELECT 1 FROM Commercial WHERE CommercialName = ? AND Status = 'active'
--
-- DEFAULT note: the COLUMN default is 'active' so the 466 EXISTING commercials
-- (already live in testing) stay detectable. The web-app REGISTRATION flow sets
-- NEW commercials to 'pending'; a nightly job flips them to 'active' when their
-- campaign StartDate arrives.
--
-- Lessons banked: SET QUOTED_IDENTIFIER ON + SET XACT_ABORT ON at the top so
-- index/computed-column writes work and any error rolls back the whole batch.
-- =============================================================================

USE RadioMonitor;
GO

SET QUOTED_IDENTIFIER ON;
SET XACT_ABORT ON;
GO

BEGIN TRANSACTION;
GO

-- Add the column (guarded; safe to re-run).
IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID('dbo.Commercial') AND name = 'Status'
)
BEGIN
    ALTER TABLE dbo.Commercial
        ADD Status NVARCHAR(20) NOT NULL
        CONSTRAINT DF_Commercial_Status DEFAULT 'active';
END
GO

-- Add the CHECK constraint (separate batch so the column exists first).
IF NOT EXISTS (SELECT 1 FROM sys.check_constraints WHERE name = 'CK_Commercial_Status')
BEGIN
    ALTER TABLE dbo.Commercial
        ADD CONSTRAINT CK_Commercial_Status
        CHECK (Status IN ('active', 'pending', 'withdrawn'));
END
GO

-- Index to make the manager's per-name active lookup and any status filtering fast.
IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_Commercial_Status' AND object_id = OBJECT_ID('dbo.Commercial')
)
BEGIN
    CREATE INDEX IX_Commercial_Status ON dbo.Commercial(Status);
END
GO

COMMIT TRANSACTION;
GO

-- =============================================================================
-- VERIFICATION
-- =============================================================================
SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, COLUMN_DEFAULT, IS_NULLABLE
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_NAME = 'Commercial' AND COLUMN_NAME = 'Status';

SELECT definition FROM sys.check_constraints WHERE name = 'CK_Commercial_Status';

SELECT name FROM sys.indexes WHERE name = 'IX_Commercial_Status';

-- Existing commercials should all be 'active' (column default applied).
SELECT Status, COUNT(*) AS N FROM dbo.Commercial GROUP BY Status;
