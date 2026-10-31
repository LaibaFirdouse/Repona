using Pay;

namespace Pay.App;

public class Cashier {
    public int Charge(int amount) => Rates.Fee(amount);
}
