package main

import (
	"encoding/json"
	"example.com/warehouse/internal/service"
	"net/http"
)

func stockHandler(w http.ResponseWriter, r *http.Request) {
	_ = json.NewEncoder(w).Encode(service.StockLevels())
}

func unusedPreviewHandler(w http.ResponseWriter, r *http.Request) {
	_ = json.NewEncoder(w).Encode(map[string]string{"preview": "yes"})
}

func main() {
	http.HandleFunc("/warehouse/stock", stockHandler)
	_ = http.ListenAndServe(":8080", nil)
}
