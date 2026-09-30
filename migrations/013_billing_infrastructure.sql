-- =============================================================================
-- RadioMonitor -- Migration 013: Billing infrastructure (Phase B1)
-- =============================================================================
-- Adds token-based billing per BILLING_DESIGN.md.
--
-- New tables:
--   SubscriberTokenAccount  -- one row per Subscriber, current balance
--   TokenTransaction        -- every token movement (purchase/debit/credit/etc)
--
-- New columns on SubscriptionPlanConfig:
--   TokensPerMonth   -- monthly allocation for Enterprise/Premium plans
--   TokenPriceUSD    -- cost per token in USD for this plan
--   ContractMonths   -- 12 (Enterprise) / 3 (Premium) / 0 (Standard/Trial)
--   TrialTokens      -- free token allocation on first signup (Trial plan only)
--
-- DESIGN: BILLING_DESIGN.md
-- =============================================================================

USE RadioMonitor;
GO
SET QUOTED_IDENTIFIER ON;
SET XACT_ABORT ON;
GO
BEGIN TRANSACTION;

-- =============================================================================
-- STEP 1: Add pricing columns to SubscriptionPlanConfig
-- =============================================================================
IF NOT EXISTS (SELECT 1 FROM sys.columns
    WHERE object_id=OBJECT_ID('dbo.SubscriptionPlanConfig') AND name='TokensPerMonth')
    ALTER TABLE dbo.SubscriptionPlanConfig ADD TokensPerMonth DECIMAL(18,4) NULL;

IF NOT EXISTS (SELECT 1 FROM sys.columns
    WHERE object_id=OBJECT_ID('dbo.SubscriptionPlanConfig') AND name='TokenPriceUSD')
    ALTER TABLE dbo.SubscriptionPlanConfig ADD TokenPriceUSD DECIMAL(10,6) NULL;

IF NOT EXISTS (SELECT 1 FROM sys.columns
    WHERE object_id=OBJECT_ID('dbo.SubscriptionPlanConfig') AND name='ContractMonths')
    ALTER TABLE dbo.SubscriptionPlanConfig ADD ContractMonths INT NOT NULL DEFAULT 0;

IF NOT EXISTS (SELECT 1 FROM sys.columns
    WHERE object_id=OBJECT_ID('dbo.SubscriptionPlanConfig') AND name='TrialTokens')
    ALTER TABLE dbo.SubscriptionPlanConfig ADD TrialTokens DECIMAL(18,4) NULL;
GO

-- =============================================================================
-- STEP 2: SubscriberTokenAccount
-- =============================================================================
IF OBJECT_ID('dbo.SubscriberTokenAccount','U') IS NULL
BEGIN
    CREATE TABLE dbo.SubscriberTokenAccount (
        AccountID       INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        SubscriberID    INT               NOT NULL UNIQUE,
        TokenBalance    DECIMAL(18,4)     NOT NULL DEFAULT 0,
        TotalPurchased  DECIMAL(18,4)     NOT NULL DEFAULT 0,
        TotalConsumed   DECIMAL(18,4)     NOT NULL DEFAULT 0,
        UpdatedAt       DATETIME2         NOT NULL DEFAULT SYSDATETIME(),

        CONSTRAINT FK_TokenAccount_Subscriber
            FOREIGN KEY (SubscriberID) REFERENCES dbo.Subscriber(SubscriberID),
        CONSTRAINT CK_TokenAccount_Balance CHECK (TokenBalance >= 0)
    );
END
GO

-- =============================================================================
-- STEP 3: TokenTransaction
-- =============================================================================
IF OBJECT_ID('dbo.TokenTransaction','U') IS NULL
BEGIN
    CREATE TABLE dbo.TokenTransaction (
        TransactionID   INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        SubscriberID    INT               NOT NULL,
        Type            NVARCHAR(20)      NOT NULL,
        TokenAmount     DECIMAL(18,4)     NOT NULL,
        BalanceAfter    DECIMAL(18,4)     NOT NULL,
        Description     NVARCHAR(500)     NULL,
        ReferenceID     INT               NULL,
        ReferenceType   NVARCHAR(20)      NULL,
        CreatedByUserID INT               NULL,
        CreatedAt       DATETIME2         NOT NULL DEFAULT SYSDATETIME(),

        CONSTRAINT FK_TokenTransaction_Subscriber
            FOREIGN KEY (SubscriberID) REFERENCES dbo.Subscriber(SubscriberID),
        CONSTRAINT FK_TokenTransaction_User
            FOREIGN KEY (CreatedByUserID) REFERENCES dbo.[User](UserID),
        CONSTRAINT CK_TokenTransaction_Type CHECK (
            Type IN ('purchase','debit','credit','refund','adjustment')),
        CONSTRAINT CK_TokenTransaction_RefType CHECK (
            ReferenceType IS NULL OR
            ReferenceType IN ('commercial','song','word','spectrum'))
    );

    CREATE INDEX IX_TokenTransaction_Subscriber
        ON dbo.TokenTransaction(SubscriberID, CreatedAt DESC);
END
GO

COMMIT TRANSACTION;
GO

-- =============================================================================
-- VERIFICATION
-- =============================================================================
PRINT '--- New tables ---';
SELECT name, create_date FROM sys.tables
WHERE name IN ('SubscriberTokenAccount','TokenTransaction')
ORDER BY name;

PRINT '--- SubscriptionPlanConfig new columns ---';
SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_NAME='SubscriptionPlanConfig'
  AND COLUMN_NAME IN ('TokensPerMonth','TokenPriceUSD','ContractMonths','TrialTokens');

PRINT '013 applied.';
