-- =============================================================================
-- RadioMonitor -- Migration 014: Add 'pending' to Subscriber status
-- =============================================================================
-- Subscribers now start as 'pending' and are activated when they accept
-- their invitation (email verified + password set). This migration:
--   1. Drops the old CK_Subscriber_Status constraint
--   2. Recreates it with 'pending' included
--   3. Changes the default from 'active' to 'pending'
-- =============================================================================

USE RadioMonitor;
GO
SET QUOTED_IDENTIFIER ON; SET XACT_ABORT ON;
GO
BEGIN TRANSACTION;

-- Drop old constraint
IF EXISTS (SELECT 1 FROM sys.check_constraints
           WHERE name='CK_Subscriber_Status'
           AND parent_object_id=OBJECT_ID('dbo.Subscriber'))
    ALTER TABLE dbo.Subscriber DROP CONSTRAINT CK_Subscriber_Status;

-- Drop old default
IF EXISTS (SELECT 1 FROM sys.default_constraints
           WHERE name='DF_Subscriber_Status'
           AND parent_object_id=OBJECT_ID('dbo.Subscriber'))
    ALTER TABLE dbo.Subscriber DROP CONSTRAINT DF_Subscriber_Status;

-- Recreate with pending included
ALTER TABLE dbo.Subscriber
    ADD CONSTRAINT CK_Subscriber_Status
    CHECK (SubscriberStatus IN ('pending','active','expired_grace','archived','cancelled'));

-- New default: pending
ALTER TABLE dbo.Subscriber
    ADD CONSTRAINT DF_Subscriber_Status DEFAULT 'pending' FOR SubscriberStatus;

COMMIT TRANSACTION;
GO

PRINT '014 applied: pending status added to Subscriber.';

-- Verify
SELECT name, definition FROM sys.check_constraints
WHERE name = 'CK_Subscriber_Status';
