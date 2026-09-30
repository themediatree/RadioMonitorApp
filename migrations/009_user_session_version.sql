-- Migration 009: Add SessionVersion to User for single-session enforcement
USE RadioMonitor;
GO
SET QUOTED_IDENTIFIER ON; SET XACT_ABORT ON;
GO
BEGIN TRANSACTION;
IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id=OBJECT_ID('dbo.[User]') AND name='SessionVersion')
    ALTER TABLE dbo.[User] ADD SessionVersion INT NOT NULL DEFAULT 1;
COMMIT TRANSACTION;
GO
PRINT '009 applied: SessionVersion added to User.';
