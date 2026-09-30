-- Migration 015: Add TranscriptionPriceUSD to SubscriptionPlanConfig
-- Separate pricing for transcription requests (cheaper than detection rate)
-- Exact price to be set by management.
USE RadioMonitor;
GO
SET QUOTED_IDENTIFIER ON; SET XACT_ABORT ON;
GO
BEGIN TRANSACTION;
IF NOT EXISTS (SELECT 1 FROM sys.columns
    WHERE object_id=OBJECT_ID('dbo.SubscriptionPlanConfig')
    AND name='TranscriptionPriceUSD')
    ALTER TABLE dbo.SubscriptionPlanConfig
        ADD TranscriptionPriceUSD DECIMAL(10,6) NULL;
COMMIT TRANSACTION;
GO
PRINT '015 applied: TranscriptionPriceUSD added to SubscriptionPlanConfig.';
