package example;
import static org.junit.jupiter.api.Assertions.assertEquals;
import org.junit.jupiter.api.Test;
class AppTest {
    @Test void returnsExpectedMessage() { assertEquals("java21-maven-consumer-ok", App.message()); }
}
