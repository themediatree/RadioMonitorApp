# Billing & Token System Design

**Status:** decided, not yet implemented.
**Agreed:** 2026-06-09

---

## 1. The core model

RadioMonitor bills by **station-hours of audio analyzed**, abstracted through
tokens.

**1 token = 1 station-hour of monitoring**

A subscriber who registers a commercial for 5 stations over 7 days consumes:
    5 stations × 168 hours = **840 tokens**

This applies equally to all subscription types:
- Commercial detection
- Song detection
- Word/phrase detection
- Frequency Spectrum Analysis

The token quantity is calculated at **registration time** and is
**fully predictable** before the subscriber commits. They know the cost before
clicking Register.

---

## 2. Plan tiers

| Plan       | Contract    | Token allocation        | USD per token | Trial tokens |
|------------|-------------|------------------------|---------------|-------------|
| Enterprise | 12 months   | N tokens/month (prepaid)| Lowest        | —           |
| Premium    | 3 months    | N tokens/month (prepaid)| Mid           | —           |
| Standard   | None        | Pay-as-you-go           | Highest       | —           |
| Trial      | None        | Free allocation (once)  | n/a           | Yes         |

- Enterprise and Premium subscribers receive their monthly token allocation
  on the 1st of each month, credited automatically to their TokenBalance.
- Standard subscribers purchase token bundles as needed.
- Trial subscribers receive a one-time free allocation on account creation.
  Trial tokens do not renew. When exhausted, the subscriber upgrades to a
  paid plan.
- Overage: if an Enterprise/Premium subscriber exhausts their monthly
  allocation mid-month, additional tokens are billed at Standard rates.

**Token prices (USD) are stored in SubscriptionPlanConfig.** Exact amounts
to be set by RadioMonitor management — the system stores and uses them but
does not hardcode them.

---

## 3. Token calculation

### Commercial registration
```
tokens = stations_count × campaign_duration_hours
campaign_duration_hours = (end_date - start_date).days × 24
                          (or a minimum of 24 if same-day)
```

### Song subscription
```
tokens = stations_count × subscription_duration_hours
```
(subscriber selects stations + duration window, same as commercial)

### Word/phrase subscription
```
tokens = stations_count × subscription_duration_hours
```

### Spectrum analysis
```
tokens = stations_count × subscription_duration_hours
```

All four use the same formula. The token cost is displayed to the subscriber
**before** they submit the registration form, so there are no surprises.

---

## 4. Database schema

### New tables

```sql
-- Token account: one row per Subscriber
CREATE TABLE SubscriberTokenAccount (
    AccountID        INT IDENTITY PRIMARY KEY,
    SubscriberID     INT NOT NULL UNIQUE FK -> Subscriber,
    TokenBalance     DECIMAL(18,4) NOT NULL DEFAULT 0,
    TotalPurchased   DECIMAL(18,4) NOT NULL DEFAULT 0,
    TotalConsumed    DECIMAL(18,4) NOT NULL DEFAULT 0,
    UpdatedAt        DATETIME2 NOT NULL DEFAULT SYSDATETIME()
);

-- Every token movement (purchase, debit, refund, monthly credit)
CREATE TABLE TokenTransaction (
    TransactionID    INT IDENTITY PRIMARY KEY,
    SubscriberID     INT NOT NULL FK -> Subscriber,
    Type             NVARCHAR(20) NOT NULL,  -- purchase|debit|credit|refund|adjustment
    TokenAmount      DECIMAL(18,4) NOT NULL, -- positive=credit, negative=debit
    BalanceAfter     DECIMAL(18,4) NOT NULL, -- snapshot for audit
    Description      NVARCHAR(500) NULL,
    ReferenceID      INT NULL,               -- CommercialID / SongSubID / etc
    ReferenceType    NVARCHAR(20) NULL,       -- commercial|song|word|spectrum
    CreatedByUserID  INT NULL FK -> [User],
    CreatedAt        DATETIME2 NOT NULL DEFAULT SYSDATETIME(),

    CONSTRAINT CK_TokenTransaction_Type CHECK (
        Type IN ('purchase','debit','credit','refund','adjustment'))
);
```

### Changes to existing tables

```sql
-- SubscriptionPlanConfig: add pricing columns
ALTER TABLE SubscriptionPlanConfig ADD
    TokensPerMonth       DECIMAL(18,4) NULL,  -- monthly alloc (Enterprise/Premium)
    TokenPriceUSD        DECIMAL(10,6) NULL,  -- USD per token for this plan
    ContractMonths       INT NOT NULL DEFAULT 0, -- 12/3/0
    TrialTokens          DECIMAL(18,4) NULL;  -- free tokens on signup (Trial plan)
```

---

## 5. Registration flow (with billing)

```
1. Subscriber fills registration form (stations, duration)
2. System calculates token cost: stations × hours
3. System fetches TokenBalance for this Subscriber
4. If balance >= cost:
   a. Create Commercial / Song / Word row (Status='pending')
   b. Debit tokens: INSERT TokenTransaction(Type='debit', TokenAmount=-cost)
   c. UPDATE SubscriberTokenAccount SET TokenBalance -= cost
   d. COMMIT (DB rows + token debit are atomic)
   e. Stage file (for commercials)
5. If balance < cost:
   Reject with: "Insufficient tokens. This registration requires X tokens;
   your balance is Y. Purchase Z more tokens to proceed."
   (Show a "Buy tokens" link)
```

The debit happens at registration time, not at detection time. The subscriber
pays for the monitoring window, not for detections found.

**Withdrawal refund:** when a subscriber withdraws a commercial before its
campaign end date, a partial refund is credited:
```
refund_tokens = stations × remaining_hours
```
This is fair and encourages subscribers to withdraw promptly rather than
leaving dead registrations consuming their balance.

---

## 6. Monthly credit job (Enterprise/Premium)

A scheduled task runs on the 1st of each month:

```python
for subscriber in active_enterprise_and_premium_subscribers():
    plan = subscriber.plan
    credit = plan.TokensPerMonth
    credit_tokens(subscriber, credit, description="Monthly allocation")
```

This is a small addition to the existing `activate_commercials.py` pattern —
a second nightly/monthly script that credits token allocations.

---

## 7. Trial token allocation

When a new Subscriber is created (via `/admin/subscribers`), if their plan
is "Trial", the system automatically creates a `SubscriberTokenAccount` row
with `TokenBalance = plan.TrialTokens`. A `TokenTransaction` row is written
with `Type='credit'` and `Description='Trial allocation'`.

This happens in `subscriber_service.create_subscriber()` — no separate step
for the admin.

---

## 8. What the subscriber sees

- **Token balance** shown on their dashboard (always visible)
- **Cost estimate** shown on every registration form before submit
  ("This registration will cost X tokens. Your balance: Y tokens.")
- **Transaction history** at `/account/tokens` — every debit, credit,
  purchase listed with description and timestamp
- **Low balance warning** when balance drops below a configurable threshold
  (e.g. < 100 tokens) — shown on dashboard and registration forms

---

## 9. What is NOT in scope for the initial billing build

- Online payment / Stripe integration (Standard subscribers purchase tokens
  via invoice for now; RadioMonitor staff credit the account manually via
  admin tools)
- Automatic overage billing (overage is flagged but not auto-charged)
- Multi-currency (USD is the billing currency; ZAR conversion is a later
  concern)
- Subscription renewal reminders / expiry notifications

These are the next phase of the billing build, once the token accounting
infrastructure is in place and working.

---

## 10. Build sequence

**Phase B1 — Token accounting infrastructure**
- Migration: SubscriberTokenAccount + TokenTransaction tables
- Migration: SubscriptionPlanConfig pricing columns
- Model + service: token_service.py (debit, credit, balance check)
- Auto-create token account when Subscriber is created
- Trial token allocation on new Trial subscriber
- Dashboard: token balance widget

**Phase B2 — Wire billing into registration flows**
- Commercial registration: cost estimate on form + debit on submit
- Song registration: same
- Word registration: same
- Withdrawal: partial refund

**Phase B3 — Admin token management**
- Manual token purchase crediting (admin UI)
- Monthly credit job for Enterprise/Premium
- Transaction history page

**Phase B4 — Self-service purchase (future)**
- Online payment integration
- Automatic overage billing
- Renewal notifications

---

## 11. Deferred decisions

- Exact token prices per plan (RadioMonitor management to decide)
- Exact trial token allocation per plan
- Monthly allocation amounts for Enterprise/Premium
- Overage policy (hard-stop vs soft-stop with notification)
- Refund policy for partial campaign withdrawal
