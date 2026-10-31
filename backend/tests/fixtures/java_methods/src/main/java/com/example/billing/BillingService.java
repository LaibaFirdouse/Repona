package com.example.billing;

import static com.example.billing.Ledger.tag;

public class BillingService {
    public String label(String id) {
        return tag(id);
    }
}
