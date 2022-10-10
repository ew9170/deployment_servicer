from kubernetes import utils, client, config, watch
import argparse
import yaml

# TODO: Figure out and delete
"""
1.) How will the user of this controller specify what deployments to watch with this controller?
    
    In all likelihood, the user will create this pod/deployment by specifying the image in a YAML.
    How will this program determine what the user wants?
    Possibilities include:
        - command line arguments (argparse)
        - somehow using command line arguments through the YAML used to create the controller (maybe with a default?)
        
2.) How will the user create the controller?
    Possibilities include:
        - YAML: kubectl apply -f "servicer.yaml"
        - kubectl run [name] --image="eatonwu/deployment_servicer"
        
3.) How do we determine whether a deployment should be serviced?
    
    The deployment should have an label that we read.
    By default, it should be "serviced: True"
    This pair should probably be in the metadata.labels section.
    
4.) How do we communicate this to users?
    
    No clue
    
5.) Labels vs Annotations

    Labels are better for this vs annotations, as labels allow users to filter deployments with certain labels
    using certain API calls such as LIST and WATCH.
    
6.) What else should we let users do?

    Services sometimes have:
        - Node ports (?)
        
    Deployments sometimes have:
        - Multiple pods (with multiple ports)
        
7.) 
"""


def is_serviced_deployment(event: dict, label=None):
    """
    Helper function to determine if the event was triggered on a marked deployment

    :param label: the key of the annotation that we want to check for
    :param event: event passed by the watch
    :return: bool; true if deployment has a flag
    """
    if label is None:
        label = "serviced"
    return label in event['object'].metadata.labels


def create_service_with_event(event: dict):
    """
    When creating a service manually (with a YAML document), we must specify:
        - .metadata.name ('deploymentname-service')
        - .spec.selector.'app.kubernetes.io/name' ('deploymentname')
        - .spec.ports[].protocol (TCP/UDP)
        - .spec.ports[].port
        - .spec.ports[].targetPort

    :param event:
    :return:
    """
    deployment_name = event
    # k8s_core_api = client.CoreV1Api
    # body = client.V1Service(
    #     api_version="v1"
    #     kind="Service"
    #     metadata=client.V1ObjectMeta(
    #
    #     )
    # )
    print("service created")


def delete_service_with_event(event: dict):
    print("service deleted")


def watch_deployments(args=None):
    """
    This function creates a Kubernetes watch object on deployments on the cluster this pod presumably runs on.
    :param args: a namespace object created by argparse.
    :return:
    """
    if args is not None:
        label = args.f
    else:
        label = None
    config.load_kube_config()
    k8s_apps_v1 = client.AppsV1Api()
    w = watch.Watch()

    for event in w.stream(k8s_apps_v1.list_deployment_for_all_namespaces):
        if event['type'] == 'ADDED':
            if is_serviced_deployment(event, label):
                create_service_with_event(event)
        if event['type'] == 'DELETED':
            if is_serviced_deployment(event, label):
                delete_service_with_event(event)


def parse_args():
    parser = argparse.ArgumentParser()

    parser.add_argument('-f', type=str, metavar='FLAG',
                        help='Optionally change the annotation used to flag deployments for servicing.'
                             ' In the YAML, the key of the annotation match the specified annotation.'
                             ' By default, the annotation is \'serviced\'')
    parser.add_argument('-p', type=str, metavar='PROTOCOL', help='The desired protocol (TCP/UDP); the argument is '
                                                                 'case-insensitive and defaults to TCP')
    parser.add_argument('--port', type=int, help='The desired port of service, argument defaults to deployment port')
    parser.add_argument('--target_port', type=int,
                        help='The desired target port (the port to forward to), argument defaults to deployment port')

    return parser.parse_args()


# the goal is to watch for new deployments (with a specific flag)
# see if deployment has an associated service
# if so, watch for if the service(s) get destroyed, if so, create a new service
# if not, create a new service
def main():
    """
    This controller will run on a Pod within a Kubernetes cluster.
    This controller tracks Deployments, and will create a Service object that has:
        - the same selector
    """
    args = parse_args()
    watch_deployments(args)


if __name__ == "__main__":
    main()
