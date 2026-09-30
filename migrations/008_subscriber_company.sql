-- Migration 008: Add CompanyName to Subscriber
-- Adds an optional CompanyName field to the Subscriber table.
-- Existing rows get NULL (acceptable -- these are test subscribers).
USE RadioMonitor;
GO
SET QUOTED_IDENTIFIER ON; SET XACT_ABORT ON;
GO
BEGIN TRANSACTION;
IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id=OBJECT_ID('dbo.Subscriber') AND name='CompanyName')
    ALTER TABLE dbo.Subscriber ADD CompanyName NVARCHAR(255) NULL;
COMMIT TRANSACTION;
GO
PRINT '008 applied: CompanyName added to Subscriber.';
