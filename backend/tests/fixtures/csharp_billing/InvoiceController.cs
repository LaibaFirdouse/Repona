using Microsoft.AspNetCore.Mvc;

namespace Billing;

[ApiController]
[Route("billing")]
public class InvoiceController : ControllerBase
{
    [HttpGet("invoices/{id}")]
    public ActionResult<string> GetInvoice(string id)
    {
        return InvoiceLedger.Lookup(id);
    }
}
