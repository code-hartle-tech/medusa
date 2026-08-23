idf_component_register(
    SRCS "medusa_lab.c"
    INCLUDE_DIRS "."
    REQUIRES esp_event esp_netif esp_wifi nvs_flash
)
