-- =============================================================================
-- RadioMonitor -- Migration 018: Reconcile GenericTranscriptionJob schema
-- =============================================================================
-- The live GenericTranscriptionJob table is missing CommercialID (our model
-- expects it) and has an extra AudioPath column (not in our model -- likely
-- added independently, possibly by the pipeline side, or from an earlier
-- draft of this table). This migration adds the missing column without
-- touching AudioPath, since something else may depend on it.
--
-- If AudioPath turns out to be unused, it can be dropped in a later cleanup
-- migration once confirmed safe.
-- =============================================================================

USE RadioMonitor;
GO
SET QUOTED_IDENTIFIER ON;
SET XACT_ABORT ON;
GO
BEGIN TRANSACTION;

IF NOT EXISTS (SELECT 1 FROM sys.columns
    WHERE object_id=OBJECT_ID('dbo.GenericTranscriptionJob') AND name='CommercialID')
BEGIN
    ALTER TABLE dbo.GenericTranscriptionJob ADD CommercialID INT NULL;

    ALTER TABLE dbo.GenericTranscriptionJob ADD
        CONSTRAINT FK_GenericTranscriptionJob_Commercial
            FOREIGN KEY (CommercialID) REFERENCES dbo.Commercial(CommercialID);
END

COMMIT TRANSACTION;
GO

PRINT '--- GenericTranscriptionJob columns after fix ---';
SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_NAME = 'GenericTranscriptionJob'
ORDER BY ORDINAL_POSITION;

PRINT '018 applied.';
