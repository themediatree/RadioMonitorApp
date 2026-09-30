-- Migration 045: Add RegistrationFee column to SubscriberServiceRate
-- Allows admins to set a flat per-registration fee (in tokens) per subscriber per service.
-- Currently used for commercial spots. NULL = no flat fee.

ALTER TABLE [dbo].[SubscriberServiceRate]
ADD [RegistrationFee] [decimal](18, 4) NULL;
GO
