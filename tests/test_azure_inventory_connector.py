from app.connectors.azure_connector import AzureConnector


def test_discover_inventory_returns_resource_lists_for_subscription(monkeypatch):
    connector = AzureConnector()
    connector.subscription_id = "sub-123"

    class FakeRGClient:
        def __init__(self, credential, subscription_id):
            self.credential = credential
            self.subscription_id = subscription_id

        def resource_groups(self):
            return []

    class FakeComputeClient:
        def __init__(self, credential, subscription_id):
            self.credential = credential
            self.subscription_id = subscription_id

        def virtual_machines(self):
            return []

    class FakeNetworkClient:
        def __init__(self, credential, subscription_id):
            self.credential = credential
            self.subscription_id = subscription_id

        def load_balancers(self):
            return []

        def network_interfaces(self):
            return []

    class FakeAksClient:
        def __init__(self, credential, subscription_id):
            self.credential = credential
            self.subscription_id = subscription_id

        def managed_clusters(self):
            return []

    monkeypatch.setattr("app.connectors.azure_connector.get_azure_credential", lambda: object())

    # The connector imports clients inside the method, so patch the module import path.
    import azure.mgmt.compute as compute_mod
    import azure.mgmt.containerservice as aks_mod
    import azure.mgmt.network as network_mod
    import azure.mgmt.resource.resources as resource_mod

    monkeypatch.setattr(compute_mod, "ComputeManagementClient", FakeComputeClient)
    monkeypatch.setattr(network_mod, "NetworkManagementClient", FakeNetworkClient)
    monkeypatch.setattr(aks_mod, "ContainerServiceClient", FakeAksClient)
    monkeypatch.setattr(resource_mod, "ResourceManagementClient", FakeRGClient)

    inventory = connector.discover_inventory(subscription_id="sub-123")

    assert inventory.subscription_id == "sub-123"
    assert inventory.resource_groups == []
    assert inventory.vms == []
    assert inventory.vnets == []
    assert inventory.load_balancers == []
    assert inventory.aks_clusters == []
