-- Migration 029: Source tracking (app vs api)
USE RadioMonitor;
GO
SET QUOTED_IDENTIFIER ON; SET XACT_ABORT ON;
GO
BEGIN TRANSACTION;

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id=OBJECT_ID('dbo.TranscriptionRequest') AND name='Source')
    ALTER TABLE dbo.TranscriptionRequest ADD Source NVARCHAR(10) NOT NULL CONSTRAINT DF_TR_Source DEFAULT 'app';

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id=OBJECT_ID('dbo.Commercial') AND name='Source')
    ALTER TABLE dbo.Commercial ADD Source NVARCHAR(10) NOT NULL CONSTRAINT DF_Commercial_Source DEFAULT 'app';

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id=OBJECT_ID('dbo.ClientSubscription') AND name='Source')
    ALTER TABLE dbo.ClientSubscription ADD Source NVARCHAR(10) NOT NULL CONSTRAINT DF_ClientSubscription_Source DEFAULT 'app';

IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id=OBJECT_ID('dbo.TokenTransaction') AND name='Source')
    ALTER TABLE dbo.TokenTransaction ADD Source NVARCHAR(10) NOT NULL CONSTRAINT DF_TokenTransaction_Source DEFAULT 'app';

COMMIT TRANSACTION;
GO
PRINT '029 applied.';
