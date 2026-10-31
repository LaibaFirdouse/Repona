package com.example.catalog;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class SkuController {
    @GetMapping("/catalog/skus")
    public String listSkus() {
        return SkuDirectory.activeCodes();
    }
}
