package service

import "example.com/warehouse/internal/store"

func StockLevels() map[string]int {
	return map[string]int{"bins": store.CountBins()}
}
