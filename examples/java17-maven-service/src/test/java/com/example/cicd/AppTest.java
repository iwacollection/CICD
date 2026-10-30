package com.example.cicd;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import org.junit.jupiter.api.Test;

class AppTest {
    @Test
    void runsOnJava17() {
        assertEquals("17", System.getProperty("java.specification.version"));
    }

    @Test
    void exposesExpectedConsumerMarker() {
        assertEquals("java17-maven-consumer-ok", App.status());
        assertTrue(App.status().contains("consumer"));
    }
}
