try:
    import torch
except ImportError:  # API backend does not need a local torch install
    torch = None


class GPUMemoryMonitor:
    def print_gpu_memory(self):
        """Prints detailed GPU memory metrics"""
        if torch is None:
            print("No local torch install - inference is running over the API")
        elif torch.cuda.is_available():
            print("\n" + "="*50)
            print("GPU MEMORY USAGE")
            print("="*50)
            total_memory = 0
            for i in range(torch.cuda.device_count()):
                alloc = torch.cuda.memory_allocated(i) / 1024**3
                reserved = torch.cuda.memory_reserved(i) / 1024**3
                total = torch.cuda.get_device_properties(i).total_memory / 1024**3
                free = total - alloc
                total_memory += total
                print(f"GPU {i}: {alloc:.2f}GB used, {free:.2f}GB free, {total:.2f}GB total")
            print(f"TOTAL GPU MEMORY: {total_memory:.2f}GB")
            print("="*50)
        else:
            print("No GPU available - using CPU")
