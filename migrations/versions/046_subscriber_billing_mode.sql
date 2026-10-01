-- Migration 046: Add BillingMode to Subscriber
-- Supports 'prepaid' (default, current behaviour) and 'postpaid' (invoice billing,
-- balance check bypassed, token language hidden from subscriber UI).

ALTER TABLE [dbo].[Subscriber]
ADD [BillingMode] VARCHAR(10) NOT NULL
    CONSTRAINT DF_Subscriber_BillingMode DEFAULT 'prepaid';

ALTER TABLE [dbo].[Subscriber]
ADD CONSTRAINT CK_Subscriber_BillingMode
    CHECK ([BillingMode] IN ('prepaid', 'postpaid'));
