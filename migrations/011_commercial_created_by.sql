-- Migration 011: Add CreatedByUserID to Commercial
USE RadioMonitor;
GO
SET QUOTED_IDENTIFIER ON; SET XACT_ABORT ON;
GO
BEGIN TRANSACTION;
IF NOT EXISTS (SELECT 1 FROM sys.columns
               WHERE object_id=OBJECT_ID('dbo.Commercial') AND name='CreatedByUserID')
    ALTER TABLE dbo.Commercial ADD CreatedByUserID INT NULL
        CONSTRAINT FK_Commercial_CreatedBy FOREIGN KEY REFERENCES dbo.[User](UserID);
COMMIT TRANSACTION;
GO
PRINT '011 applied: CreatedByUserID added to Commercial.';
