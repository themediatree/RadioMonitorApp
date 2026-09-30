-- =============================================================================
-- RadioMonitor -- Migration 026: Billing configuration
-- =============================================================================
-- Per-plan ZAR token price + global USD exchange rate (display-only).
-- Admin manages via /admin/billing/. Defaults to ZAR 3.60/token all plans.
-- =============================================================================

USE RadioMonitor;
GO
SET QUOTED_IDENTIFIER ON; SET XACT_ABORT ON;
GO
BEGIN TRANSACTION;

IF OBJECT_ID('dbo.BillingConfig','U') IS NULL
BEGIN
    CREATE TABLE dbo.BillingConfig (
        ConfigID        INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        PlanCode        NVARCHAR(50)      NOT NULL UNIQUE,
        ZARPerToken     DECIMAL(10,4)     NOT NULL DEFAULT 3.60,
        UpdatedAt       DATETIME2         NOT NULL
            CONSTRAINT DF_BillingConfig_UpdatedAt DEFAULT SYSDATETIME(),
        UpdatedByUserID INT               NULL,

        CONSTRAINT FK_BillingConfig_Plan
            FOREIGN KEY (PlanCode) REFERENCES dbo.SubscriptionPlanConfig(PlanCode)
    );

    -- Default rows for the three plans
    INSERT INTO dbo.BillingConfig (PlanCode, ZARPerToken)
    SELECT PlanCode, 3.60
    FROM dbo.SubscriptionPlanConfig
    WHERE IsActive = 1;
END

-- Global exchange rate table (one row, admin-editable)
IF OBJECT_ID('dbo.ExchangeRate','U') IS NULL
BEGIN
    CREATE TABLE dbo.ExchangeRate (
        RateID          INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        CurrencyPair    NVARCHAR(10)      NOT NULL UNIQUE, -- e.g. 'ZAR/USD'
        Rate            DECIMAL(10,4)     NOT NULL,        -- ZAR per 1 USD
        UpdatedAt       DATETIME2         NOT NULL
            CONSTRAINT DF_ExchangeRate_UpdatedAt DEFAULT SYSDATETIME(),
        UpdatedByUserID INT               NULL
    );

    INSERT INTO dbo.ExchangeRate (CurrencyPair, Rate)
    VALUES ('ZAR/USD', 18.50);
END

COMMIT TRANSACTION;
GO

PRINT '--- BillingConfig ---';
SELECT PlanCode, ZARPerToken FROM dbo.BillingConfig;

PRINT '--- ExchangeRate ---';
SELECT CurrencyPair, Rate FROM dbo.ExchangeRate;

PRINT '026 applied.';
