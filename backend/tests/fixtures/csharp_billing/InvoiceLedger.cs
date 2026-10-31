namespace Billing;

public static class InvoiceLedger
{
    public static string Lookup(string id)
    {
        return $"INV-{id}";
    }
}
