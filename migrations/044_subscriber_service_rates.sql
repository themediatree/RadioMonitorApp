-- Migration 044: per-subscriber service rate multipliers
-- Adds SubscriberServiceRate table.
-- Absence of a row = use DEFAULT_SERVICE_RATES in token_service.py.

CREATE TABLE SubscriberServiceRate (
    RateID        INT IDENTITY PRIMARY KEY,
    SubscriberID  INT NOT NULL
                      REFERENCES Subscriber(SubscriberID)
                      ON DELETE CASCADE,
    ServiceType   VARCHAR(20) NOT NULL,  -- commercial / song / word / transcription / spectrum
    RatePerHour   DECIMAL(10, 4) NOT NULL,
    SetByUserID   INT NULL,
    Notes         NVARCHAR(500) NULL,
    CreatedAt     DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),

    CONSTRAINT UQ_SubServiceRate UNIQUE (SubscriberID, ServiceType),
    CONSTRAINT CK_SubServiceRate_Type CHECK (
        ServiceType IN ('commercial', 'song', 'word', 'transcription', 'spectrum')
    ),
    CONSTRAINT CK_SubServiceRate_Rate CHECK (RatePerHour >= 0)
);
