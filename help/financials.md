# Financial Data

The **Financials** system tracks financial transactions and data within a case. This includes bank transactions, invoices, payments, and other money flows.

## Financial Records

Each financial record has the following fields:

| Field | Description |
|-------|-------------|
| **Type** | Credit (income) or Debit (expense) |
| **Amount** | Amount in euros |
| **Date** | Date of the transaction |
| **Description** | Description of the transaction |
| **Counterparty** | Name of the counterparty |
| **Category** | Category (Salary, Invoice, Transfer, etc.) |
| **Source** | Source of the data (bank statement, invoice, etc.) |
| **Reference** | Reference number / transaction ID |

## Adding Financial Data

1. Open the case detail page
2. Scroll to the **Financials** section
3. Click **Add Financial Record**
4. Fill in the details
5. Click **Save**

## Overview

The financials section shows:

- All records in a table sorted by date
- Total amount (sum of all credit/debit records)
- Pagination (20 records per page)

## Categories

Available categories can be extended in the admin settings.

## Usage

Financials are typically used for:

- Recording suspicious transactions
- Documenting payment flows
- Tracking investigation costs
- Generating financial overviews for reports
